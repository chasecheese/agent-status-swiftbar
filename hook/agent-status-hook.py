#!/usr/bin/env python3
"""Claude Code hook -> per-session state file for the SwiftBar plugin.

Usage: agent-status-hook.py <state>

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

# agentstatus.py is deployed alongside this script in ~/.claude/scripts/.
sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
from agentstatus import (  # noqa: E402
    PROMPT_MAX_LEN, STATE_DIR,
    find_terminal_app, get_tty_of, latest_ai_title,
    load_config, maybe_notify,
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


def _carry_over(target) -> tuple[str, str, str, str, list | None]:
    """Pull (state, prompt, terminal_app, tty, notify_states) from prior write.

    ``notify_states`` is the per-session notification override (list, possibly
    empty) or ``None`` when the user hasn't toggled anything for this session.
    ``state`` is used to gate transition-fired notifications.
    """
    if not target.exists():
        return "", "", "", "", None
    try:
        old = json.loads(target.read_text())
    except Exception:
        return "", "", "", "", None
    raw_notify = old.get("notify_states")
    notify_states = raw_notify if isinstance(raw_notify, list) else None
    return (
        old.get("state", "") or "",
        old.get("prompt", "") or "",
        old.get("terminal_app", "") or "",
        old.get("tty", "") or "",
        notify_states,
    )


def main() -> int:
    if len(sys.argv) < 2:
        return 0
    state = sys.argv[1]
    # Optional `--source=<claude|codex>` lets the same hook serve both
    # agents (Claude Code wires it without the flag → defaults to claude;
    # Codex CLI's hooks.json passes --source=codex). The source ends up
    # in the state file so the plugin can label the row's origin later.
    source = "claude"
    for arg in sys.argv[2:]:
        if arg.startswith("--source="):
            source = arg.split("=", 1)[1] or "claude"
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

    (prev_state, prev_prompt, prev_terminal,
     prev_tty, prev_notify_states) = _carry_over(target)
    prompt = (payload.get("prompt") or "").strip() or prev_prompt
    ppid = os.getppid()
    transcript_path = payload.get("transcript_path", "")
    summary = latest_ai_title(transcript_path)
    cwd = payload.get("cwd", "")

    record = {
        "state":           state,
        "source":          source,
        "session_id":      session_id,
        "cwd":             cwd,
        "transcript_path": transcript_path,
        "message":         payload.get("message", ""),
        "prompt":          prompt[:PROMPT_MAX_LEN],
        "summary":         summary[:PROMPT_MAX_LEN],
        "terminal_app":    prev_terminal or find_terminal_app(ppid),
        "tty":             prev_tty or get_tty_of(ppid),
        "notify_states":   prev_notify_states,  # None or list, persisted as-is
        # The hook's PPID is the agent (claude/codex) process. Storing it
        # lets the plugin prune sessions whose agent has since exited even
        # without a SessionEnd hook (Codex CLI doesn't emit one; Claude
        # may also miss it on a hard quit).
        "agent_pid":       ppid,
        "since":           int(time.time()),
    }
    _atomic_write(target, record)

    # Fire desktop notification on transitions enabled for this session.
    try:
        maybe_notify(state, prev_state, summary, cwd, record,
                     load_config()["notifications"])
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main() or 0)
    except Exception:
        sys.exit(0)
