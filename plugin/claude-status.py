#!/usr/bin/env python3
"""SwiftBar plugin entry — renders the menu bar status from state files.

Invoked by the deployed bash wrapper at every SwiftBar refresh tick. All
shared logic lives in ``claudebar.py`` (deployed to ``~/.claude/scripts/``);
this file only assembles SwiftBar-flavoured stdout.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
from claudebar import (  # noqa: E402
    APP_BRAND_STATE_PILLS, APP_LOGOS, IDE_BIN, MESSAGE_MAX_LEN,
    STATE_BRAND_LOGOS, STATE_LABELS, SUMMARY_MAX_LEN, TOGGLE_PATH,
    aggregate_state, effective_enabled_states, load_config, read_state_files,
)


def _lookup_ci(d: dict, key: str):
    """Case-insensitive dict lookup. Older state files sometimes wrote the
    host name in a different case than APP_LOGOS / APP_BRAND_STATE_PILLS
    keys (e.g. `claude` vs `Claude`); normalize at read time."""
    if not key:
        return None
    if key in d:
        return d[key]
    for k, v in d.items():
        if k.lower() == key.lower():
            return v
    return None


# ── Helpers ──────────────────────────────────────────────────────────────────
def humanage(now: int, since: int) -> str:
    s = max(0, now - int(since or 0))
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m"
    return f"{s // 3600}h"


def truncate(s: str, n: int) -> str:
    s = s.replace("\n", " ").replace("|", "/")
    return s[: n - 1] + "…" if len(s) > n else s


def osa_params(*lines: str) -> str:
    """Build SwiftBar params for ``osascript -e <line> -e <line> ...``."""
    parts = ["bash='/usr/bin/osascript'"]
    for i, line in enumerate(lines):
        escaped = line.replace("'", "'\\''")
        parts.append(f"param{i * 2 + 1}='-e'")
        parts.append(f"param{i * 2 + 2}='{escaped}'")
    parts.append("terminal=false")
    return " ".join(parts)


def click_action(terminal_app: str, tty: str, cwd: str) -> str:
    """SwiftBar params for the row click — best-effort jump to source window.

    - Terminal.app / iTerm: per-tab focus by tty
    - VS Code / Cursor / Windsurf: ``<bin> --reuse-window <cwd>``
    - Other GUI apps: ``tell application "<NAME>" to activate``
    - Nothing detected: empty (caller falls back to opening cwd in Finder)
    """
    if terminal_app == "Terminal" and tty:
        return osa_params(
            'tell application "Terminal"',
            '  activate',
            '  repeat with w in windows',
            '    try',
            f'      set t to (first tab of w whose tty is "{tty}")',
            '      set frontmost of w to true',
            '      set selected of t to true',
            '      return',
            '    end try',
            '  end repeat',
            'end tell',
        )
    if terminal_app == "iTerm" and tty:
        return osa_params(
            'tell application "iTerm"',
            '  activate',
            '  repeat with w in windows',
            '    repeat with t in tabs of w',
            '      repeat with s in sessions of t',
            f'        if tty of s is "{tty}" then',
            '          select t',
            '          select w',
            '          return',
            '        end if',
            '      end repeat',
            '    end repeat',
            '  end repeat',
            'end tell',
        )
    if terminal_app in IDE_BIN and cwd:
        bin_path = IDE_BIN[terminal_app]
        if Path(bin_path).exists():
            return f"bash='{bin_path}' param1='--reuse-window' param2='{cwd}' terminal=false"
    if terminal_app:
        return osa_params(f'tell application "{terminal_app}" to activate')
    return ""


# ── Rendering ────────────────────────────────────────────────────────────────
def render_header(records: list[dict], header_icons: dict, priority: list[str]) -> None:
    agg = aggregate_state(records, priority)
    print(f"| sfimage={header_icons.get(agg, 'circle')}")


def render_notify_toggles(record: dict, priority: list[str],
                          notifications: dict, notify_icons: dict) -> None:
    """Per-session notification toggles. Writes to the session's state file."""
    session_id = (record.get("session_id") or "").strip()
    if not session_id:
        return
    enabled = set(effective_enabled_states(record, notifications))
    for state in priority:
        on = state in enabled
        label = STATE_LABELS.get(state, state)
        icon = notify_icons.get(state, "circle")
        # Native NSMenu checkbox: `checked=true|false` sets state=.on/off,
        # which gives a real macOS checkmark and reserves the checkmark
        # column for the whole submenu so unchecked rows align under
        # checked ones.
        checked = "true" if on else "false"
        print(
            f"-- {label} | checked={checked} sfimage={icon} "
            f"bash='{TOGGLE_PATH}' param1='--session' param2='{session_id}' "
            f"param3='--state' param4='{state}' "
            f"refresh=true terminal=false"
        )


def render_row(r: dict, now: int, icons: dict, priority: list[str],
               notifications: dict, notify_icons: dict,
               action_icons: dict) -> None:
    state = r.get("state", "?")
    cwd = r.get("cwd", "") or ""
    short_cwd = Path(cwd).name if cwd else ""
    msg = (r.get("message") or "").strip()
    summary_text = (r.get("summary") or "").strip() \
                   or (r.get("prompt") or "").strip() \
                   or short_cwd or "(no cwd)"
    summary_text = truncate(summary_text, SUMMARY_MAX_LEN)

    label = STATE_LABELS.get(state, state)
    age = humanage(now, r.get("since", 0))
    state_icon = icons.get(state, "circle")
    source = (r.get("source") or "claude").strip().lower()

    terminal_app = (r.get("terminal_app") or "").strip()
    tty = (r.get("tty") or "").strip()
    click = click_action(terminal_app, tty, cwd)

    # Row image — three priority tiers:
    #   1) 3-icon (host + brand + state) full-colour pill, with light + dark
    #      variants. SwiftBar's `image=<light>,<dark>` syntax picks one
    #      based on the system Appearance, so we don't shell out per tick.
    #      Source is 88×32 (2×); displayed at 44×16 logical.
    #   2) 2-icon (brand + state) template pill — auto-tinted by NSMenu.
    #      Source 96×48 (3×); displayed 32×16. Used when the host isn't in
    #      our bundled APP_LOGOS set.
    #   3) Plain state SF Symbol (config-driven), if the brand isn't
    #      claude/codex either.
    pill3 = _lookup_ci(APP_BRAND_STATE_PILLS, terminal_app)
    pill3 = (pill3 or {}).get(source, {}).get(state)
    pill2 = STATE_BRAND_LOGOS.get(source, {}).get(state)
    line = f"{label}  {summary_text}  ({age} ago)"
    if pill3:
        params = [
            f"image={pill3['light']},{pill3['dark']} width=44 height=16",
        ]
    elif pill2:
        params = [f"templateImage={pill2} width=32 height=16"]
    else:
        params = [f"sfimage={state_icon}"]
    if click:
        params.append(click)
    elif cwd:
        params.append(f"href=file://{cwd}")
    print(f"{line} | {' '.join(params)}")

    if msg:
        msg_short = msg.replace(chr(10), " ")[:MESSAGE_MAX_LEN]
        print(f"-- {msg_short} | sfimage={action_icons['message']} color=gray size=11")
    if cwd:
        print(
            f"-- Open Folder | sfimage={action_icons['open_folder']} "
            f"bash='/usr/bin/open' param1='{cwd}' terminal=false"
        )
    if click:
        # Prefer the actual host app's icon (full-color PNG bundled in
        # APP_LOGOS) so the row visually identifies the destination.
        # Lookup is case-insensitive as a safety net for legacy state
        # files written before name-canonicalisation. Fall back to the
        # configurable SF Symbol when no bundled logo matches.
        app_logo = APP_LOGOS.get(terminal_app)
        if not app_logo and terminal_app:
            ci_match = next(
                (v for k, v in APP_LOGOS.items() if k.lower() == terminal_app.lower()),
                None,
            )
            app_logo = ci_match
        icon_attr = (
            f"image={app_logo} width=16 height=16"
            if app_logo
            else f"sfimage={action_icons['return_to_tab']}"
        )
        print(f"-- Return to Tab | {icon_attr} {click}")
    print("-----")
    render_notify_toggles(r, priority, notifications, notify_icons)


def main() -> None:
    cfg = load_config()
    records = read_state_files()

    render_header(records, cfg["header_icons"], cfg["priority"])
    print("---")

    if not records:
        print("No active Claude Code sessions")
    else:
        now = int(time.time())
        for r in records:
            render_row(r, now, cfg["icons"], cfg["priority"],
                       cfg["notifications"], cfg["notify_icons"],
                       cfg["action_icons"])

    print("---")
    print("Refresh | refresh=true")


if __name__ == "__main__":
    main()
