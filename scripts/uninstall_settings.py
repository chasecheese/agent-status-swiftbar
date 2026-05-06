#!/usr/bin/env python3
"""Remove claude-code-swiftbar hooks from ~/.claude/settings.json."""
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

HOOK_PY = str(Path.home() / ".claude" / "scripts" / "claude-swiftbar-hook.py")


def is_ours(cmd) -> bool:
    return isinstance(cmd, str) and HOOK_PY in cmd


def main() -> int:
    settings_path = Path.home() / ".claude" / "settings.json"
    if not settings_path.exists():
        print("No settings.json — nothing to do.")
        return 0

    backup = settings_path.with_suffix(".json.bak")
    shutil.copy2(settings_path, backup)
    print(f"  backup  -> {backup}")

    settings = json.loads(settings_path.read_text())

    hooks = settings.get("hooks", {})
    removed = 0
    for event in list(hooks.keys()):
        entries = hooks.get(event)
        if not isinstance(entries, list):
            continue
        for entry in entries:
            sub = entry.get("hooks", [])
            kept = [h for h in sub if not is_ours(h.get("command", ""))]
            removed += len(sub) - len(kept)
            entry["hooks"] = kept
        # Drop empty matcher entries.
        entries[:] = [e for e in entries if e.get("hooks")]
        if not entries:
            del hooks[event]

    fd, tmp = tempfile.mkstemp(dir=settings_path.parent, prefix=".settings.", suffix=".json")
    with os.fdopen(fd, "w") as f:
        json.dump(settings, f, indent=2)
        f.write("\n")
    os.replace(tmp, settings_path)

    print(f"  cleaned -> {settings_path} ({removed} hook(s) removed)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
