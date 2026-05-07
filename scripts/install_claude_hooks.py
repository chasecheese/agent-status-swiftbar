#!/usr/bin/env python3
"""Patch ~/.claude/settings.json with our hook routes.

Reads ``~/.claude/swiftbar-config.json`` (key ``events``) to decide which
Claude Code hook events get wired up and which state name they write.
Idempotent — hooks belonging to other tools are left alone, ours are
stripped and rebuilt on every run so config edits propagate cleanly.

Each event in the config maps to one of:
- a string state name        -> single matcher='' route
- a dict {matcher: state}    -> per-matcher routes (e.g. Notification)
- null / "" / missing        -> not wired up (any prior wiring of ours
                                for that event is stripped)
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "lib"))
from agentstatus import (  # noqa: E402
    ALL_EVENTS, HOOK_PATH, PYTHON, SETTINGS_PATH, load_config,
)


def make_command(state: str) -> str:
    return f'"{PYTHON}" "{HOOK_PATH}" {state}'


def is_ours(cmd) -> bool:
    return isinstance(cmd, str) and str(HOOK_PATH) in cmd


def normalize_routes(value) -> list[tuple[str, str]]:
    """Turn an event config value into a list of ``(matcher, state)`` pairs."""
    if value is None or value == "":
        return []
    if isinstance(value, str):
        return [("", value)]
    if isinstance(value, dict):
        return [
            (m, s) for m, s in value.items()
            if isinstance(m, str) and isinstance(s, str) and s
        ]
    return []


def upsert_event(hooks: dict, event: str, routes: list[tuple[str, str]]) -> bool:
    """Strip our prior commands for ``event``, then install fresh routes.

    Returns True if anything changed.
    """
    entries = hooks.setdefault(event, [])
    changed = False

    # Strip our previous commands from any existing matcher entry.
    for e in entries:
        sub = e.get("hooks", [])
        kept = [h for h in sub if not is_ours(h.get("command", ""))]
        if kept != sub:
            e["hooks"] = kept
            changed = True
    new_entries = [e for e in entries if e.get("hooks")]
    if new_entries != entries:
        entries[:] = new_entries
        changed = True

    if not routes:
        if not entries:
            del hooks[event]
        return changed

    for matcher, state in routes:
        target = next((e for e in entries if e.get("matcher", "") == matcher), None)
        if target is None:
            target = {"matcher": matcher, "hooks": []}
            entries.append(target)
        target.setdefault("hooks", []).append({
            "type": "command",
            "command": make_command(state),
        })
        changed = True

    return changed


def main() -> int:
    if SETTINGS_PATH.exists():
        backup = SETTINGS_PATH.with_suffix(".json.bak")
        shutil.copy2(SETTINGS_PATH, backup)
        print(f"  backup -> {backup}")
        try:
            settings = json.loads(SETTINGS_PATH.read_text())
        except Exception as e:
            print(f"ERROR: {SETTINGS_PATH} is not valid JSON: {e}", file=sys.stderr)
            return 1
    else:
        settings = {}

    events_cfg = load_config()["claude_events"]
    hooks = settings.setdefault("hooks", {})
    changed = 0
    for event in ALL_EVENTS:
        if upsert_event(hooks, event, normalize_routes(events_cfg.get(event))):
            changed += 1

    fd, tmp = tempfile.mkstemp(dir=SETTINGS_PATH.parent, prefix=".settings.", suffix=".json")
    with os.fdopen(fd, "w") as f:
        json.dump(settings, f, indent=2)
        f.write("\n")
    os.replace(tmp, SETTINGS_PATH)

    print(f"  wrote   -> {SETTINGS_PATH} ({changed} event(s) updated)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
