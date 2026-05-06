#!/usr/bin/env python3
"""Add claude-code-swiftbar hooks to ~/.claude/settings.json.

Idempotent. Existing hooks (e.g. another tool's clawd-hook) are preserved —
we append our command to the matcher='' entry of each event we care about.
"""
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

PYTHON = "/usr/bin/python3"
HOOK_PY = str(Path.home() / ".claude" / "scripts" / "claude-swiftbar-hook.py")

# Map Claude Code hook event -> state name we write into the state file.
EVENTS = {
    "SessionStart":     "idle",
    "SessionEnd":       "end",
    "UserPromptSubmit": "working",
    "Stop":             "done",
    "Notification":     "waiting",
}


def make_command(state: str) -> str:
    return f'"{PYTHON}" "{HOOK_PY}" {state}'


def is_ours(cmd) -> bool:
    return isinstance(cmd, str) and HOOK_PY in cmd


def main() -> int:
    settings_path = Path.home() / ".claude" / "settings.json"
    if settings_path.exists():
        backup = settings_path.with_suffix(".json.bak")
        shutil.copy2(settings_path, backup)
        print(f"  backup -> {backup}")
        try:
            settings = json.loads(settings_path.read_text())
        except Exception as e:
            print(f"ERROR: {settings_path} is not valid JSON: {e}", file=sys.stderr)
            return 1
    else:
        settings = {}

    hooks = settings.setdefault("hooks", {})
    changed = 0

    for event, state in EVENTS.items():
        cmd = make_command(state)
        entries = hooks.setdefault(event, [])

        # Find the matcher='' entry, or create it.
        target = next((e for e in entries if e.get("matcher", "") == ""), None)
        if target is None:
            target = {"matcher": "", "hooks": []}
            entries.append(target)

        sub = target.setdefault("hooks", [])

        # Strip any stale claude-code-swiftbar commands (e.g. wrong state arg).
        cleaned = [h for h in sub if not is_ours(h.get("command", ""))]
        cleaned.append({"type": "command", "command": cmd})
        if cleaned != sub:
            sub[:] = cleaned
            changed += 1

    fd, tmp = tempfile.mkstemp(dir=settings_path.parent, prefix=".settings.", suffix=".json")
    with os.fdopen(fd, "w") as f:
        json.dump(settings, f, indent=2)
        f.write("\n")
    os.replace(tmp, settings_path)

    print(f"  wrote   -> {settings_path} ({changed} event(s) updated)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
