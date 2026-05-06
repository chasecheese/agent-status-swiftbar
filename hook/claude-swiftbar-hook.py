#!/usr/bin/env python3
"""Claude Code hook -> per-session state file for the SwiftBar plugin.

Usage: claude-swiftbar-hook.py <state>

Reads the hook payload from stdin and writes
``~/.claude/state/swiftbar/<session_id>.json``. State value ``end`` deletes
the file. Failures are swallowed so a broken hook never blocks Claude Code.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time

# claudebar.py is deployed alongside this script in ~/.claude/scripts/.
sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
from claudebar import (  # noqa: E402
    PROMPT_MAX_LEN, STATE_DIR,
    find_terminal_app, get_tty_of, latest_ai_title,
)


def _read_payload() -> dict:
    raw = sys.stdin.read()
    try:
        return json.loads(raw) if raw.strip() else {}
    except Exception:
        return {}


def _atomic_write(target, record: dict) -> None:
    fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=f".{target.stem}.", suffix=".json")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(record, f)
        os.replace(tmp, target)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def _carry_over(target) -> tuple[str, str, str]:
    """Pull (prompt, terminal_app, tty) from any existing state file.

    These three are detected/captured once per session and persisted across
    every subsequent hook fire so the dropdown stays informative regardless
    of which hook event most recently wrote the state.
    """
    if not target.exists():
        return "", "", ""
    try:
        old = json.loads(target.read_text())
    except Exception:
        return "", "", ""
    return (
        old.get("prompt", "") or "",
        old.get("terminal_app", "") or "",
        old.get("tty", "") or "",
    )


def main() -> int:
    if len(sys.argv) < 2:
        return 0
    state = sys.argv[1]
    payload = _read_payload()

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

    prev_prompt, prev_terminal, prev_tty = _carry_over(target)
    prompt = (payload.get("prompt") or "").strip() or prev_prompt
    ppid = os.getppid()
    transcript_path = payload.get("transcript_path", "")

    record = {
        "state":           state,
        "session_id":      session_id,
        "cwd":             payload.get("cwd", ""),
        "transcript_path": transcript_path,
        "message":         payload.get("message", ""),
        "prompt":          prompt[:PROMPT_MAX_LEN],
        "summary":         latest_ai_title(transcript_path)[:PROMPT_MAX_LEN],
        "terminal_app":    prev_terminal or find_terminal_app(ppid),
        "tty":             prev_tty or get_tty_of(ppid),
        "since":           int(time.time()),
    }
    _atomic_write(target, record)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main() or 0)
    except Exception:
        sys.exit(0)
