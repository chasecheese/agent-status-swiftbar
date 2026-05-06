#!/usr/bin/env python3
"""Claude Code hook -> per-session state file for the SwiftBar plugin.

Usage: claude-swiftbar-hook.py <state>

Reads the hook payload from stdin (Claude Code passes a JSON object) and
writes ~/.claude/state/swiftbar/<session_id>.json. State value `end` deletes
the file. Failures are swallowed so a broken hook never blocks Claude Code.
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

STATE_DIR = Path.home() / ".claude" / "state" / "swiftbar"

# Pull the outermost `<NAME>.app/Contents/...` segment out of a process's
# comm path. macOS ps reports full paths like
#   /Applications/Ghostty.app/Contents/MacOS/ghostty
#   /Applications/Visual Studio Code.app/Contents/Frameworks/Code Helper.app/...
# `re.search` returns the first match → the outer app, which is what
# AppleScript `tell application "<NAME>"` expects.
APP_PATH_RE = re.compile(r"/([^/]+)\.app/Contents/")

# Comm-name -> AppleScript-friendly app name overrides. Only needed when the
# .app-path inference returns the wrong name (rare). Empty by default.
APP_NAME_OVERRIDES = {
    "iTerm2": "iTerm",  # binary inside iTerm.app/ is named "iTerm2"
}


def get_tty_of(pid: int) -> str:
    """Return the controlling tty of `pid` (e.g. /dev/ttys015) or empty."""
    try:
        out = subprocess.run(
            ["ps", "-p", str(pid), "-o", "tty="],
            capture_output=True, text=True, timeout=2,
        ).stdout.strip()
    except Exception:
        return ""
    if not out or out in ("?", "??"):
        return ""
    return out if out.startswith("/dev/") else f"/dev/{out}"


def latest_ai_title(transcript_path: str) -> str:
    """Pull the latest `ai-title` Claude Code wrote into the transcript.

    Reads only the tail of the JSONL to keep this cheap on long sessions.
    Returns "" if the title hasn't been generated yet (early in a session).
    """
    if not transcript_path:
        return ""
    p = Path(transcript_path)
    if not p.exists():
        return ""
    try:
        size = p.stat().st_size
        window = min(128 * 1024, size)
        with p.open("rb") as f:
            f.seek(size - window)
            tail = f.read().decode("utf-8", errors="replace")
        title = ""
        for line in tail.splitlines():
            try:
                r = json.loads(line)
            except Exception:
                continue
            if r.get("type") == "ai-title":
                t = (r.get("aiTitle") or "").strip()
                if t:
                    title = t
        return title
    except Exception:
        return ""


def find_terminal_app(start_pid: int) -> str:
    """Walk up the parent process chain looking for a GUI app bundle."""
    try:
        out = subprocess.run(
            ["ps", "-A", "-o", "pid=,ppid=,comm="],
            capture_output=True, text=True, timeout=2,
        ).stdout
    except Exception:
        return ""
    procs = {}
    for line in out.splitlines():
        parts = line.split(None, 2)
        if len(parts) < 3:
            continue
        try:
            pid, ppid = int(parts[0]), int(parts[1])
        except ValueError:
            continue
        procs[pid] = (ppid, parts[2])

    pid = start_pid
    for _ in range(20):
        if pid not in procs:
            return ""
        ppid, comm = procs[pid]
        m = APP_PATH_RE.search(comm)
        if m:
            return APP_NAME_OVERRIDES.get(m.group(1), m.group(1))
        if ppid <= 1:
            return ""
        pid = ppid
    return ""


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

    # UserPromptSubmit ships the user's text in payload.prompt. Capture it,
    # otherwise preserve whatever the previous write recorded so the prompt
    # stays visible across PreToolUse/Notification/Stop/etc. Same trick keeps
    # terminal_app pinned (terminal doesn't change for a session).
    prompt = (payload.get("prompt") or "").strip()
    prev_terminal = ""
    prev_tty = ""
    try:
        if target.exists():
            old = json.loads(target.read_text())
            if not prompt:
                prompt = old.get("prompt", "")
            prev_terminal = old.get("terminal_app", "")
            prev_tty = old.get("tty", "")
    except Exception:
        pass

    ppid = os.getppid()
    terminal_app = prev_terminal or find_terminal_app(ppid)
    tty = prev_tty or get_tty_of(ppid)

    transcript_path = payload.get("transcript_path", "")
    summary = latest_ai_title(transcript_path)

    record = {
        "state": state,
        "session_id": session_id,
        "cwd": payload.get("cwd", ""),
        "transcript_path": transcript_path,
        "message": payload.get("message", ""),
        "prompt": prompt[:200],
        "summary": summary[:200],
        "terminal_app": terminal_app,
        "tty": tty,
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
