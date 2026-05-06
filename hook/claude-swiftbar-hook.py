#!/usr/bin/env python3
"""Claude Code hook -> per-session state file for the SwiftBar plugin.

Usage: claude-swiftbar-hook.py <state>

Reads the hook payload from stdin (Claude Code passes a JSON object) and
writes ~/.claude/state/swiftbar/<session_id>.json. State value `end` deletes
the file. Failures are swallowed so a broken hook never blocks Claude Code.
"""
import json
import os
import sys
import tempfile
import time
from pathlib import Path

STATE_DIR = Path.home() / ".claude" / "state" / "swiftbar"


def main() -> int:
    if len(sys.argv) < 2:
        return 0
    state = sys.argv[1]

    raw = sys.stdin.read()
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except Exception:
        payload = {}

    session_id = payload.get("session_id") or ""
    if not session_id:
        return 0

    STATE_DIR.mkdir(parents=True, exist_ok=True)
    target = STATE_DIR / f"{session_id}.json"

    if state == "end":
        try:
            target.unlink()
        except FileNotFoundError:
            pass
        return 0

    record = {
        "state": state,
        "session_id": session_id,
        "cwd": payload.get("cwd", ""),
        "transcript_path": payload.get("transcript_path", ""),
        "message": payload.get("message", ""),
        "since": int(time.time()),
    }

    fd, tmp = tempfile.mkstemp(dir=STATE_DIR, prefix=f".{session_id}.", suffix=".json")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(record, f)
        os.replace(tmp, target)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main() or 0)
    except Exception:
        sys.exit(0)
