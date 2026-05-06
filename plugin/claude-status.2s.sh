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
    "done":    "checkmark.circle.fill",
    "idle":    "circle",
    "none":    "circle.dotted",
}
DEFAULT_PRIORITY = ["asking", "notify", "working", "done", "idle"]


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
        "done":    "DONE",
        "idle":    "IDLE",
    }
    for r in records:
        state = r.get("state", "?")
        cwd = r.get("cwd", "") or ""
        short_cwd = Path(cwd).name if cwd else "(no cwd)"
        msg = r.get("message", "") or ""
        since = r.get("since", 0)
        label = state_label.get(state, state)
        line = f"{label}  {short_cwd}  ({humanage(since)} ago)"
        line = line.replace("|", "/")
        params = [f"sfimage={ICONS.get(state, 'circle')}"]
        if cwd:
            params.append(f"href=file://{cwd}")
        print(f"{line} | {' '.join(params)}")
        if msg:
            short = msg.replace("\n", " ")[:120]
            print(f"-- {short} | color=gray size=11")
        if cwd:
            print(f"-- Open folder | bash='/usr/bin/open' param1='{cwd}' terminal=false")

print("---")
print("Refresh | refresh=true")
PY
