#!/usr/bin/env bash
# <bitbar.title>Claude Code Status</bitbar.title>
# <bitbar.version>0.1</bitbar.version>
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

ICONS = {
    "waiting": ":exclamationmark.circle.fill:",
    "working": ":hourglass:",
    "done":    ":checkmark.circle.fill:",
    "idle":    ":circle:",
    "none":    ":circle.dotted:",
}
COLORS = {
    "waiting": "#ff453a",
    "working": "#ffd60a",
    "done":    "#30d158",
    "idle":    "#8e8e93",
    "none":    "#8e8e93",
}
PRIORITY = ["waiting", "working", "done", "idle"]

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

count = len(records)
header_icon = ICONS[agg]
header_color = COLORS[agg]
header_text = f"{count}" if count > 1 else ""
header = f"{header_text} | sfimage={header_icon[1:-1]} color={header_color}"
print(header.strip())

print("---")

if not records:
    print("No active Claude Code sessions | color=gray")
else:
    def humanage(ts):
        s = max(0, now - int(ts or 0))
        if s < 60:
            return f"{s}s"
        if s < 3600:
            return f"{s // 60}m"
        return f"{s // 3600}h"

    state_label = {
        "waiting": "WAITING",
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
        # SwiftBar params: avoid pipe in body
        line = line.replace("|", "/")
        params = [f"color={COLORS.get(state, '#8e8e93')}"]
        if cwd:
            params.append(f"href=file://{cwd}")
        sfsym = ICONS.get(state, ":circle:")[1:-1]
        params.append(f"sfimage={sfsym}")
        print(f"{line} | {' '.join(params)}")
        if msg:
            short = msg.replace("\n", " ")[:120]
            print(f"-- {short} | color=gray size=11")
        if cwd:
            print(f"-- Open folder | bash='/usr/bin/open' param1='{cwd}' terminal=false")

print("---")
print("Refresh | refresh=true")
PY
