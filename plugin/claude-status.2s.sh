#!/usr/bin/env bash
# <bitbar.title>Claude Code Status</bitbar.title>
# <bitbar.version>0.5</bitbar.version>
# <bitbar.author>local</bitbar.author>
# <bitbar.desc>Aggregate state across active Claude Code sessions.</bitbar.desc>
# <swiftbar.hideAbout>true</swiftbar.hideAbout>
# <swiftbar.hideRunInTerminal>true</swiftbar.hideRunInTerminal>
# <swiftbar.hideDisablePlugin>true</swiftbar.hideDisablePlugin>
# <swiftbar.hideSwiftBar>true</swiftbar.hideSwiftBar>

exec /usr/bin/python3 - <<'PY'
import json
import time
from pathlib import Path

STATE_DIR = Path.home() / ".claude" / "state" / "swiftbar"
CONFIG_PATH = Path.home() / ".claude" / "swiftbar-config.json"

DEFAULT_ICONS = {
    "asking": "exclamationmark.circle.fill",
    "notify":  "bell.circle.fill",
    "working": "hourglass",
    "waiting":    "checkmark.circle.fill",
    "idle":    "circle",
    "none":    "circle.dotted",
}
DEFAULT_PRIORITY = ["asking", "notify", "working", "waiting", "idle"]

IDE_BIN = {
    "Visual Studio Code": "/Applications/Visual Studio Code.app/Contents/Resources/app/bin/code",
    "Cursor":             "/Applications/Cursor.app/Contents/Resources/app/bin/cursor",
    "Windsurf":           "/Applications/Windsurf.app/Contents/Resources/app/bin/windsurf",
}


def _osa_params(*lines):
    """Build SwiftBar params for `osascript -e <line> -e <line> ...`."""
    parts = ["bash='/usr/bin/osascript'"]
    n = 1
    for line in lines:
        escaped = line.replace("'", "'\\''")
        parts.append(f"param{n}='-e'")
        n += 1
        parts.append(f"param{n}='{escaped}'")
        n += 1
    parts.append("terminal=false")
    return " ".join(parts)


def click_action(terminal_app, tty, cwd):
    """Return SwiftBar params (str) for the row click. Empty if nothing useful."""
    if terminal_app == "Terminal" and tty:
        return _osa_params(
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
        return _osa_params(
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
        return _osa_params(f'tell application "{terminal_app}" to activate')
    return ""


def load_config():
    icons = dict(DEFAULT_ICONS)
    priority = list(DEFAULT_PRIORITY)
    try:
        cfg = json.loads(CONFIG_PATH.read_text())
    except Exception:
        return icons, priority
    if isinstance(cfg, dict):
        ic = cfg.get("icons")
        if isinstance(ic, dict):
            icons.update({k: v for k, v in ic.items() if isinstance(v, str) and v})
        pr = cfg.get("priority")
        if isinstance(pr, list):
            valid = [s for s in pr if isinstance(s, str) and s in icons and s != "none"]
            if valid:
                priority = valid
    return icons, priority


ICONS, PRIORITY = load_config()

records = []
if STATE_DIR.exists():
    for f in sorted(STATE_DIR.glob("*.json")):
        try:
            r = json.loads(f.read_text())
            records.append(r)
        except Exception:
            pass

# Stale guard: ignore session files older than 12h
now = int(time.time())
records = [r for r in records if now - int(r.get("since", 0) or 0) < 12 * 3600]

agg = "none"
for s in PRIORITY:
    if any(r.get("state") == s for r in records):
        agg = s
        break

print(f"| sfimage={ICONS.get(agg, 'circle')}")
print("---")

if not records:
    print("No active Claude Code sessions")
else:
    def humanage(ts):
        s = max(0, now - int(ts or 0))
        if s < 60:
            return f"{s}s"
        if s < 3600:
            return f"{s // 60}m"
        return f"{s // 3600}h"

    state_label = {
        "asking": "ASKING",
        "notify":  "NOTIFY",
        "working": "WORKING",
        "waiting":    "WAITING",
        "idle":    "IDLE",
    }
    for r in records:
        state = r.get("state", "?")
        cwd = r.get("cwd", "") or ""
        short_cwd = Path(cwd).name if cwd else ""
        msg = r.get("message", "") or ""
        ai_summary = (r.get("summary") or "").strip()
        prompt = (r.get("prompt") or "").strip()
        terminal_app = (r.get("terminal_app") or "").strip()
        tty = (r.get("tty") or "").strip()
        since = r.get("since", 0)
        label = state_label.get(state, state)

        summary = ai_summary or prompt or short_cwd or "(no cwd)"
        summary = summary.replace("\n", " ").replace("|", "/")
        if len(summary) > 80:
            summary = summary[:79] + "…"

        line = f"{label}  {summary}  ({humanage(since)} ago)"
        params = [f"sfimage={ICONS.get(state, 'circle')}"]
        click = click_action(terminal_app, tty, cwd)
        if click:
            params.append(click)
        elif cwd:
            params.append(f"href=file://{cwd}")
        print(f"{line} | {' '.join(params)}")

        if prompt and short_cwd:
            print(f"-- in {short_cwd} | color=gray size=11")
        if msg:
            short = msg.replace("\n", " ")[:120]
            print(f"-- {short} | color=gray size=11")
        if cwd:
            print(f"-- Open folder | bash='/usr/bin/open' param1='{cwd}' terminal=false")

print("---")
print("Refresh | refresh=true")
PY
