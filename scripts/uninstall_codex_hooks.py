#!/usr/bin/env python3
"""Remove our hook routes from ~/.codex/hooks.json."""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "lib"))
from agentstatus import CODEX_HOOKS_PATH, HOOK_PATH  # noqa: E402


def is_ours(cmd) -> bool:
    return isinstance(cmd, str) and str(HOOK_PATH) in cmd and "--source=codex" in cmd


def main() -> int:
    if not CODEX_HOOKS_PATH.exists():
        print("No ~/.codex/hooks.json — nothing to do.")
        return 0

    backup = CODEX_HOOKS_PATH.with_suffix(".json.bak")
    shutil.copy2(CODEX_HOOKS_PATH, backup)
    print(f"  backup  -> {backup}")

    settings = json.loads(CODEX_HOOKS_PATH.read_text())
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

    fd, tmp = tempfile.mkstemp(dir=CODEX_HOOKS_PATH.parent, prefix=".hooks.", suffix=".json")
    with os.fdopen(fd, "w") as f:
        json.dump(settings, f, indent=2)
        f.write("\n")
    os.replace(tmp, CODEX_HOOKS_PATH)

    print(f"  cleaned -> {CODEX_HOOKS_PATH} ({removed} hook(s) removed)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
