"""Shared utilities for claude-code-swiftbar.

Single source of truth for filesystem paths, default state taxonomy,
process-tree introspection, transcript parsing, and config loading.

Imported by:
- hook/claude-swiftbar-hook.py     (writes per-session state files)
- plugin/claude-status.py          (renders SwiftBar dropdown)
- scripts/install_settings.py      (patches ~/.claude/settings.json)

Designed to fail soft: every helper that touches the filesystem catches
its own exceptions and returns an empty/default value rather than raising.
A broken helper must never crash the Claude Code hook chain.
"""
from __future__ import annotations

import json
import re
import subprocess
import time
from pathlib import Path

# ── Paths ────────────────────────────────────────────────────────────────────
HOME = Path.home()
CLAUDE_DIR = HOME / ".claude"

STATE_DIR = CLAUDE_DIR / "state" / "swiftbar"
CONFIG_PATH = CLAUDE_DIR / "swiftbar-config.json"
SETTINGS_PATH = CLAUDE_DIR / "settings.json"
SCRIPTS_DIR = CLAUDE_DIR / "scripts"
HOOK_PATH = SCRIPTS_DIR / "claude-swiftbar-hook.py"
PLUGIN_PY_PATH = SCRIPTS_DIR / "claude-swiftbar-plugin.py"

PYTHON = "/usr/bin/python3"

# ── Default state taxonomy ───────────────────────────────────────────────────
DEFAULT_REFRESH_INTERVAL_MS = 1000
MIN_REFRESH_INTERVAL_MS = 100

DEFAULT_ICONS = {
    "asking":  "exclamationmark.bubble.circle.fill",
    "notify":  "bell.circle.fill",
    "working": "hourglass.circle.fill",
    "waiting": "circle.badge.checkmark",
    "idle":    "circle",
    "none":    "circle.dotted",
}
DEFAULT_PRIORITY = ["asking", "notify", "working", "waiting", "idle"]

# Full official Claude Code hook surface. install_settings.py iterates this so
# disabling an event in the user config (set null) actually removes our prior
# wiring instead of leaving a stale entry behind.
ALL_EVENTS = (
    "SessionStart",
    "UserPromptSubmit",
    "PreToolUse",
    "PostToolUse",
    "Notification",
    "Stop",
    "SubagentStop",
    "PreCompact",
    "SessionEnd",
)
DEFAULT_EVENTS = {
    "SessionStart":     "idle",
    "UserPromptSubmit": "working",
    "PreToolUse":       "working",
    "PostToolUse":      "working",
    "Notification": {
        "permission_prompt":  "asking",
        "elicitation_dialog": "asking",
        "idle_prompt":        None,
        "auth_success":       "notify",
    },
    "Stop":             "waiting",
    "SubagentStop":     None,
    "PreCompact":       None,
    "SessionEnd":       "end",
}
STATE_LABELS = {
    "asking":  "ASKING",
    "notify":  "NOTIFY",
    "working": "WORKING",
    "waiting": "WAITING",
    "idle":    "IDLE",
}

# Sessions older than this are treated as dead (Claude Code may have crashed
# without firing SessionEnd, leaving a stale state file behind).
STALE_AGE_S = 12 * 3600

# Caps on user-supplied strings that get embedded into state files / dropdown.
PROMPT_MAX_LEN = 200
SUMMARY_MAX_LEN = 80
MESSAGE_MAX_LEN = 120
TRANSCRIPT_TAIL_BYTES = 128 * 1024


# ── Host app detection ───────────────────────────────────────────────────────
# Pull `<NAME>.app/Contents/...` out of a process's comm path. macOS reports
# full paths like `/Applications/Ghostty.app/Contents/MacOS/ghostty`, so the
# first regex match yields the outer .app — exactly what AppleScript's
# `tell application "<NAME>"` expects.
APP_PATH_RE = re.compile(r"/([^/]+)\.app/Contents/")

# Override map for cases where the .app folder name doesn't match the
# AppleScript scripting name (rare).
APP_NAME_OVERRIDES = {
    "iTerm2": "iTerm",
}

# IDEs that ship a `--reuse-window <path>` CLI; we use that instead of plain
# `tell ... to activate` so clicks land on the right project window.
IDE_BIN = {
    "Visual Studio Code": "/Applications/Visual Studio Code.app/Contents/Resources/app/bin/code",
    "Cursor":             "/Applications/Cursor.app/Contents/Resources/app/bin/cursor",
    "Windsurf":           "/Applications/Windsurf.app/Contents/Resources/app/bin/windsurf",
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


def find_terminal_app(start_pid: int) -> str:
    """Walk up the parent process chain looking for a GUI app bundle.

    Returns the .app's display name (e.g. ``"Ghostty"``, ``"Visual Studio Code"``)
    or ``""`` if nothing in the chain is hosted by an .app bundle.
    """
    try:
        out = subprocess.run(
            ["ps", "-A", "-o", "pid=,ppid=,comm="],
            capture_output=True, text=True, timeout=2,
        ).stdout
    except Exception:
        return ""
    procs: dict[int, tuple[int, str]] = {}
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
    for _ in range(20):  # safety cap on chain length
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


def latest_ai_title(transcript_path: str) -> str:
    """Return the most recent ``ai-title`` Claude Code wrote to the transcript.

    Reads only the tail of the JSONL (~128 KB) to keep this cheap on long
    sessions. Empty string if the title hasn't been generated yet.
    """
    if not transcript_path:
        return ""
    p = Path(transcript_path)
    if not p.exists():
        return ""
    try:
        size = p.stat().st_size
        window = min(TRANSCRIPT_TAIL_BYTES, size)
        with p.open("rb") as f:
            f.seek(size - window)
            tail = f.read().decode("utf-8", errors="replace")
    except Exception:
        return ""
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


# ── Config ───────────────────────────────────────────────────────────────────
def _coerce_interval(value) -> int:
    try:
        ms = int(value)
    except Exception:
        return DEFAULT_REFRESH_INTERVAL_MS
    return max(MIN_REFRESH_INTERVAL_MS, ms)


def load_config(path: Path | None = None) -> dict:
    """Read swiftbar-config.json and return a fully-populated config dict.

    Always returns a dict with the four top-level keys
    (``refresh_interval_ms``, ``icons``, ``priority``, ``events``);
    missing or malformed sections fall back to defaults.

    Pass ``path`` to read from a non-default location (``install.sh`` uses
    this to read the repo's seed config before deployment).
    """
    src = path or CONFIG_PATH
    cfg: dict = {}
    try:
        loaded = json.loads(src.read_text())
        if isinstance(loaded, dict):
            cfg = loaded
    except Exception:
        cfg = {}

    icons = dict(DEFAULT_ICONS)
    if isinstance(cfg.get("icons"), dict):
        icons.update({k: v for k, v in cfg["icons"].items() if isinstance(v, str) and v})

    priority = list(DEFAULT_PRIORITY)
    pr = cfg.get("priority")
    if isinstance(pr, list):
        valid = [s for s in pr if isinstance(s, str) and s in icons and s != "none"]
        if valid:
            priority = valid

    events = dict(DEFAULT_EVENTS)
    if isinstance(cfg.get("events"), dict):
        events.update(cfg["events"])

    return {
        "refresh_interval_ms": _coerce_interval(cfg.get("refresh_interval_ms",
                                                         DEFAULT_REFRESH_INTERVAL_MS)),
        "icons": icons,
        "priority": priority,
        "events": events,
    }


def plugin_filename_for(refresh_interval_ms: int) -> str:
    """Encode a refresh interval as the SwiftBar plugin filename suffix.

    SwiftBar reliably parses both ``Ns`` and ``Nms`` filename suffixes.
    Fractional seconds (``0.5s``) are not honored uniformly across versions,
    so anything that isn't a whole number of seconds becomes ``Nms``.
    """
    ms = max(MIN_REFRESH_INTERVAL_MS, int(refresh_interval_ms))
    suffix = f"{ms // 1000}s" if ms % 1000 == 0 else f"{ms}ms"
    return f"claude-status.{suffix}.sh"


# ── State files ──────────────────────────────────────────────────────────────
def read_state_files() -> list[dict]:
    """Return per-session state records, oldest first, stale ones filtered."""
    if not STATE_DIR.exists():
        return []
    records: list[dict] = []
    for f in sorted(STATE_DIR.glob("*.json")):
        try:
            records.append(json.loads(f.read_text()))
        except Exception:
            continue
    now = int(time.time())
    return [r for r in records if now - int(r.get("since", 0) or 0) < STALE_AGE_S]


def aggregate_state(records: list[dict], priority: list[str]) -> str:
    """Pick the highest-priority state across active sessions.

    Falls back to ``"none"`` when no record matches anything in ``priority``.
    """
    for s in priority:
        if any(r.get("state") == s for r in records):
            return s
    return "none"
