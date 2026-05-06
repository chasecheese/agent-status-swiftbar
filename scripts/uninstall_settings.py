#!/usr/bin/env python3
"""Remove claude-code-swiftbar hooks from ~/.claude/settings.json."""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "lib"))
from claudebar import HOOK_PATH, SETTINGS_PATH  # noqa: E402


def is_ours(cmd) -> bool:
    return isinstance(cmd, str) and str(HOOK_PATH) in cmd


def main() -> int:
    if not SETTINGS_PATH.exists():
        print("No settings.json — nothing to do.")
        return 0

    backup = SETTINGS_PATH.with_suffix(".json.bak")
    shutil.copy2(SETTINGS_PATH, backup)
    print(f"  backup  -> {backup}")

    settings = json.loads(SETTINGS_PATH.read_text())
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
        entries[:] = [e for e in entries if e.get("hooks")]
        if not entries:
            del hooks[event]

    fd, tmp = tempfile.mkstemp(dir=SETTINGS_PATH.parent, prefix=".settings.", suffix=".json")
    with os.fdopen(fd, "w") as f:
        json.dump(settings, f, indent=2)
        f.write("\n")
    os.replace(tmp, SETTINGS_PATH)

    print(f"  cleaned -> {SETTINGS_PATH} ({removed} hook(s) removed)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
