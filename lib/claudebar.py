"""Shared utilities for claude-code-swiftbar.

Single source of truth for filesystem paths, default state taxonomy,
process-tree introspection, transcript parsing, and config loading.

Imported by:
- hook/claude-swiftbar-hook.py     (writes per-session state files)
- plugin/claude-status.py          (renders SwiftBar dropdown)
- scripts/install_settings.py      (patches ~/.claude/settings.json)

Designed to fail soft: every helper that touches the filesystem catches
its own exceptions and returns an empty/default value rather than raising.
A broken helper must never crash the Claude Code hook chain.
"""
from __future__ import annotations

import json
import re
import shlex
import shutil
import subprocess
import time
from pathlib import Path

# ── Paths ────────────────────────────────────────────────────────────────────
HOME = Path.home()
CLAUDE_DIR = HOME / ".claude"

STATE_DIR = CLAUDE_DIR / "state" / "swiftbar"
CONFIG_PATH = CLAUDE_DIR / "swiftbar-config.json"
SETTINGS_PATH = CLAUDE_DIR / "settings.json"
SCRIPTS_DIR = CLAUDE_DIR / "scripts"
HOOK_PATH = SCRIPTS_DIR / "claude-swiftbar-hook.py"
PLUGIN_PY_PATH = SCRIPTS_DIR / "claude-swiftbar-plugin.py"
TOGGLE_PATH = SCRIPTS_DIR / "claude-swiftbar-toggle.py"

PYTHON = "/usr/bin/python3"
OSASCRIPT = "/usr/bin/osascript"

# ── Default state taxonomy ───────────────────────────────────────────────────
DEFAULT_REFRESH_INTERVAL_MS = 1000
MIN_REFRESH_INTERVAL_MS = 100

# ── State taxonomy ───────────────────────────────────────────────────────────
# Three live states, plus `none` for the empty-aggregate menu-bar header.
# A fresh session opens as `waiting` (no separate `idle`).
STATE_LABELS = {
    "asking":  "ASKING",
    "working": "WORKING",
    "waiting": "WAITING",
}
DEFAULT_ICONS = {
    "asking":  "exclamationmark.bubble.circle.fill",
    "working": "hourglass.circle.fill",
    "waiting": "circle.badge.checkmark",
    "none":    "circle.dotted",
}
DEFAULT_PRIORITY = ["asking", "working", "waiting"]
DEFAULT_NOTIFICATIONS = {
    # No notifications enabled out of the box — opt in via the dropdown's
    # per-session toggles (writes notify_states into that session's state).
    "enabled_states":   [],
    "sound":            False,
    "sound_name":       "Glass",
    "include_summary":  True,
}

# Full official Claude Code hook surface. install_settings.py iterates this so
# disabling an event in the user config (set null) actually removes our prior
# wiring instead of leaving a stale entry behind.
ALL_EVENTS = (
    "SessionStart",
    "UserPromptSubmit",
    "PreToolUse",
    "PostToolUse",
    "Notification",
    "Stop",
    "SubagentStop",
    "PreCompact",
    "SessionEnd",
)
DEFAULT_EVENTS = {
    "SessionStart":     "waiting",     # fresh session → waiting
    "UserPromptSubmit": "working",
    "PreToolUse":       "working",
    "PostToolUse":      "working",
    "Notification": {
        "permission_prompt":  "asking",
        "elicitation_dialog": "asking",
        "idle_prompt":        None,
        "auth_success":       None,
    },
    "Stop":             "waiting",
    "SubagentStop":     None,
    "PreCompact":       None,
    "SessionEnd":       "end",
}

# Sessions older than this are treated as dead (Claude Code may have crashed
# without firing SessionEnd, leaving a stale state file behind).
STALE_AGE_S = 12 * 3600

# Caps on user-supplied strings that get embedded into state files / dropdown.
PROMPT_MAX_LEN = 200
SUMMARY_MAX_LEN = 80
MESSAGE_MAX_LEN = 120
TRANSCRIPT_TAIL_BYTES = 128 * 1024


# ── Host app detection ───────────────────────────────────────────────────────
# Pull `<NAME>.app/Contents/...` out of a process's comm path. macOS reports
# full paths like `/Applications/Ghostty.app/Contents/MacOS/ghostty`, so the
# first regex match yields the outer .app — exactly what AppleScript's
# `tell application "<NAME>"` expects.
APP_PATH_RE = re.compile(r"/([^/]+)\.app/Contents/")

# Override map for cases where the .app folder name doesn't match the
# AppleScript scripting name (rare).
APP_NAME_OVERRIDES = {
    "iTerm2": "iTerm",
}

# IDEs that ship a `--reuse-window <path>` CLI; we use that instead of plain
# `tell ... to activate` so clicks land on the right project window.
IDE_BIN = {
    "Visual Studio Code": "/Applications/Visual Studio Code.app/Contents/Resources/app/bin/code",
    "Cursor":             "/Applications/Cursor.app/Contents/Resources/app/bin/cursor",
    "Windsurf":           "/Applications/Windsurf.app/Contents/Resources/app/bin/windsurf",
}


def get_tty_of(pid: int) -> str:
    """Return the controlling tty of `pid` (e.g. /dev/ttys015) or empty."""
    try:
        out = subprocess.run(
            ["ps", "-p", str(pid), "-o", "tty="],
            capture_output=True, text=True, timeout=2,
        ).stdout.strip()
    except Exception:
        return ""
    if not out or out in ("?", "??"):
        return ""
    return out if out.startswith("/dev/") else f"/dev/{out}"


def find_terminal_app(start_pid: int) -> str:
    """Walk up the parent process chain looking for a GUI app bundle.

    Returns the .app's display name (e.g. ``"Ghostty"``, ``"Visual Studio Code"``)
    or ``""`` if nothing in the chain is hosted by an .app bundle.
    """
    try:
        out = subprocess.run(
            ["ps", "-A", "-o", "pid=,ppid=,comm="],
            capture_output=True, text=True, timeout=2,
        ).stdout
    except Exception:
        return ""
    procs: dict[int, tuple[int, str]] = {}
    for line in out.splitlines():
        parts = line.split(None, 2)
        if len(parts) < 3:
            continue
        try:
            pid, ppid = int(parts[0]), int(parts[1])
        except ValueError:
            continue
        procs[pid] = (ppid, parts[2])

    pid = start_pid
    for _ in range(20):  # safety cap on chain length
        if pid not in procs:
            return ""
        ppid, comm = procs[pid]
        m = APP_PATH_RE.search(comm)
        if m:
            return APP_NAME_OVERRIDES.get(m.group(1), m.group(1))
        if ppid <= 1:
            return ""
        pid = ppid
    return ""


def latest_ai_title(transcript_path: str) -> str:
    """Return the most recent ``ai-title`` Claude Code wrote to the transcript.

    Reads only the tail of the JSONL (~128 KB) to keep this cheap on long
    sessions. Empty string if the title hasn't been generated yet.
    """
    if not transcript_path:
        return ""
    p = Path(transcript_path)
    if not p.exists():
        return ""
    try:
        size = p.stat().st_size
        window = min(TRANSCRIPT_TAIL_BYTES, size)
        with p.open("rb") as f:
            f.seek(size - window)
            tail = f.read().decode("utf-8", errors="replace")
    except Exception:
        return ""
    title = ""
    for line in tail.splitlines():
        try:
            r = json.loads(line)
        except Exception:
            continue
        if r.get("type") == "ai-title":
            t = (r.get("aiTitle") or "").strip()
            if t:
                title = t
    return title


# ── Config ───────────────────────────────────────────────────────────────────
def _filter_events(user_events: dict, allowed_states: set) -> dict:
    """Strip user event entries that route to unknown states.

    Strings: dropped unless the named state is in the vocabulary
    (sentinels ``end`` / ``""`` / ``None`` are always kept).
    Dicts (per-matcher routes): each matcher entry is kept iff its target
    state is allowed. The whole event entry is preserved so legitimate
    matchers can still take effect even when one is dropped.
    """
    out: dict = {}
    for ev_name, value in user_events.items():
        if value is None or value == "":
            out[ev_name] = value
        elif isinstance(value, str):
            if value in allowed_states or value == "end":
                out[ev_name] = value
            # else: drop, fall back to base default
        elif isinstance(value, dict):
            cleaned = {}
            for matcher, target in value.items():
                if target is None or target == "":
                    cleaned[matcher] = target
                elif isinstance(target, str) and (target in allowed_states or target == "end"):
                    cleaned[matcher] = target
            out[ev_name] = cleaned
        else:
            out[ev_name] = value
    return out


def _coerce_interval(value) -> int:
    try:
        ms = int(value)
    except Exception:
        return DEFAULT_REFRESH_INTERVAL_MS
    return max(MIN_REFRESH_INTERVAL_MS, ms)


def load_config(path: Path | None = None) -> dict:
    """Read swiftbar-config.json and return a fully-populated config dict.

    Always returns the same shape regardless of what's on disk; missing or
    malformed sections fall back to defaults. User-supplied entries that
    name states outside the known vocabulary are silently dropped.

    Pass ``path`` to read from a non-default location (``install.sh`` uses
    this to read the repo's seed config before deployment).
    """
    src = path or CONFIG_PATH
    cfg: dict = {}
    try:
        loaded = json.loads(src.read_text())
        if isinstance(loaded, dict):
            cfg = loaded
    except Exception:
        cfg = {}

    allowed_states = set(DEFAULT_ICONS)

    icons = dict(DEFAULT_ICONS)
    if isinstance(cfg.get("icons"), dict):
        for k, v in cfg["icons"].items():
            if isinstance(v, str) and v and k in allowed_states:
                icons[k] = v

    # Menu-bar header icons: optional separate map. Falls back to the
    # row-icons set per state if `header_icons` is missing or doesn't
    # specify a particular state.
    header_icons = dict(icons)
    if isinstance(cfg.get("header_icons"), dict):
        for k, v in cfg["header_icons"].items():
            if isinstance(v, str) and v and k in allowed_states:
                header_icons[k] = v

    # Per-state icons used by the dropdown's "Notify on: …" toggles.
    # Falls back to the row icons same way header_icons does.
    notify_icons = dict(icons)
    if isinstance(cfg.get("notify_icons"), dict):
        for k, v in cfg["notify_icons"].items():
            if isinstance(v, str) and v and k in allowed_states:
                notify_icons[k] = v

    priority = list(DEFAULT_PRIORITY)
    pr = cfg.get("priority")
    if isinstance(pr, list):
        valid = [s for s in pr if isinstance(s, str) and s in allowed_states and s != "none"]
        if valid:
            priority = valid

    events = dict(DEFAULT_EVENTS)
    if isinstance(cfg.get("events"), dict):
        events.update(_filter_events(cfg["events"], allowed_states))

    notifications = dict(DEFAULT_NOTIFICATIONS)
    if isinstance(cfg.get("notifications"), dict):
        user_notif = cfg["notifications"]
        states = user_notif.get("enabled_states")
        if isinstance(states, list):
            notifications["enabled_states"] = [
                s for s in states if isinstance(s, str) and s in icons
            ]
        for key in ("sound", "include_summary"):
            if isinstance(user_notif.get(key), bool):
                notifications[key] = user_notif[key]
        sound_name = user_notif.get("sound_name")
        if isinstance(sound_name, str) and sound_name:
            notifications["sound_name"] = sound_name

    return {
        "refresh_interval_ms": _coerce_interval(cfg.get("refresh_interval_ms",
                                                         DEFAULT_REFRESH_INTERVAL_MS)),
        "icons": icons,
        "header_icons": header_icons,
        "notify_icons": notify_icons,
        "priority": priority,
        "events": events,
        "notifications": notifications,
    }


def plugin_filename_for(refresh_interval_ms: int) -> str:
    """Encode a refresh interval as the SwiftBar plugin filename suffix.

    SwiftBar reliably parses both ``Ns`` and ``Nms`` filename suffixes.
    Fractional seconds (``0.5s``) are not honored uniformly across versions,
    so anything that isn't a whole number of seconds becomes ``Nms``.
    """
    ms = max(MIN_REFRESH_INTERVAL_MS, int(refresh_interval_ms))
    suffix = f"{ms // 1000}s" if ms % 1000 == 0 else f"{ms}ms"
    return f"claude-status.{suffix}.sh"


# ── State files ──────────────────────────────────────────────────────────────
def read_state_files() -> list[dict]:
    """Return per-session state records, oldest first, stale ones filtered."""
    if not STATE_DIR.exists():
        return []
    records: list[dict] = []
    for f in sorted(STATE_DIR.glob("*.json")):
        try:
            records.append(json.loads(f.read_text()))
        except Exception:
            continue
    now = int(time.time())
    return [r for r in records if now - int(r.get("since", 0) or 0) < STALE_AGE_S]


def _osa_quote(s: str) -> str:
    """Wrap a string as an AppleScript literal with proper escaping."""
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _osa_shell_command(*lines: str) -> str:
    """Build a single shell command string that runs `osascript -e <line> ...`.

    Each line is shell-quoted so the result is safe to embed in another
    shell-string slot (notably terminal-notifier's ``-execute`` argument).
    """
    parts = [OSASCRIPT]
    for line in lines:
        parts.append("-e")
        parts.append(shlex.quote(line))
    return " ".join(parts)


def click_command_shell(terminal_app: str, tty: str, cwd: str) -> str:
    """Build a shell command that focuses the originating window/tab.

    Used by ``terminal-notifier -execute`` so a notification click jumps
    back to the right place. Returns ``""`` when no useful action is
    available (e.g. unrecognised host or missing tty for tab focus).
    """
    if terminal_app == "Terminal" and tty:
        return _osa_shell_command(
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
        return _osa_shell_command(
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
            return f"{shlex.quote(bin_path)} --reuse-window {shlex.quote(cwd)}"
    if terminal_app:
        return _osa_shell_command(f'tell application "{terminal_app}" to activate')
    return ""


def effective_enabled_states(record: dict, notifications: dict) -> list[str]:
    """Enabled states for one session. Per-session override > global default.

    The override lives in the session's state file as ``notify_states``;
    a list (even empty) is treated as authoritative. Missing / non-list
    falls back to the global ``enabled_states``.
    """
    v = record.get("notify_states")
    if isinstance(v, list):
        return [s for s in v if isinstance(s, str)]
    return list(notifications.get("enabled_states") or [])


def maybe_notify(new_state: str, prev_state: str, summary: str, cwd: str,
                 record: dict, notifications: dict) -> None:
    """Fire a macOS desktop notification if this state transition is enabled.

    Skips when state hasn't actually changed (avoids spam from PreToolUse /
    PostToolUse rewriting the same state every tool call). Per-session
    ``notify_states`` from ``record`` overrides the global default.
    """
    if not new_state or new_state == prev_state:
        return
    enabled = set(effective_enabled_states(record, notifications))
    if new_state not in enabled:
        return

    title = f"Claude Code · {STATE_LABELS.get(new_state, new_state.upper())}"
    body = ""
    if notifications.get("include_summary", True):
        body = (summary or (Path(cwd).name if cwd else "")).strip()
    body = body[:160]
    sound_name = notifications.get("sound_name", "Glass")

    # Prefer terminal-notifier when present — it ships its own bundle ID, so
    # macOS lets the user grant "Banners" / "Alerts" without having to dig
    # into Script Editor's notification settings. It also supports `-execute`
    # for click-to-focus, which osascript cannot do.
    tn = shutil.which("terminal-notifier")
    if tn:
        args = [tn, "-title", title, "-message", body]
        if notifications.get("sound"):
            args += ["-sound", sound_name]
        click_cmd = click_command_shell(
            (record.get("terminal_app") or "").strip(),
            (record.get("tty") or "").strip(),
            cwd,
        )
        if click_cmd:
            args += ["-execute", click_cmd]
        try:
            subprocess.run(args, capture_output=True, timeout=3)
            return
        except Exception:
            pass  # fall through to osascript

    script = f"display notification {_osa_quote(body)} with title {_osa_quote(title)}"
    if notifications.get("sound"):
        script += f" sound name {_osa_quote(sound_name)}"
    try:
        subprocess.run([OSASCRIPT, "-e", script], capture_output=True, timeout=3)
    except Exception:
        pass


def aggregate_state(records: list[dict], priority: list[str]) -> str:
    """Pick the highest-priority state across active sessions.

    Falls back to ``"none"`` when no record matches anything in ``priority``.
    """
    for s in priority:
        if any(r.get("state") == s for r in records):
            return s
    return "none"
