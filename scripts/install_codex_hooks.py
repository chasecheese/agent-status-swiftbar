#!/usr/bin/env python3
"""Patch ~/.codex/hooks.json with our hook routes.

Codex CLI's hook configuration lives in either ``hooks.json`` or under
``[hooks]`` in ``config.toml``. This patcher uses the JSON form because
it's a dedicated file we own — easier to leave the user's config.toml
alone and remain forward-compatible with whatever Codex adds there.

Reads ``~/.claude/swiftbar-config.json`` (key ``codex_events``) to decide
which Codex hook events get wired up. Idempotent — our previous routes
are stripped on every run, then rebuilt from config so edits propagate
cleanly. Routes belonging to other tools are left alone.

Each event in the config maps to one of:
- a string state name        -> single matcher='' route
- a dict {matcher: state}    -> per-matcher routes
- null / "" / missing        -> not wired up
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
    ALL_CODEX_EVENTS, CODEX_DIR, CODEX_HOOKS_PATH,
    HOOK_PATH, PYTHON, load_config,
)


def make_command(state: str) -> str:
    return f'"{PYTHON}" "{HOOK_PATH}" {state} --source=codex'


def is_ours(cmd) -> bool:
    return isinstance(cmd, str) and str(HOOK_PATH) in cmd and "--source=codex" in cmd


def normalize_routes(value) -> list[tuple[str, str]]:
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
    entries = hooks.setdefault(event, [])
    changed = False

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
    CODEX_DIR.mkdir(parents=True, exist_ok=True)
    if CODEX_HOOKS_PATH.exists():
        backup = CODEX_HOOKS_PATH.with_suffix(".json.bak")
        shutil.copy2(CODEX_HOOKS_PATH, backup)
        print(f"  backup -> {backup}")
        try:
            settings = json.loads(CODEX_HOOKS_PATH.read_text())
        except Exception as e:
            print(f"ERROR: {CODEX_HOOKS_PATH} is not valid JSON: {e}", file=sys.stderr)
            return 1
    else:
        settings = {}

    events_cfg = load_config()["codex_events"]
    hooks = settings.setdefault("hooks", {})
    changed = 0
    for event in ALL_CODEX_EVENTS:
        if upsert_event(hooks, event, normalize_routes(events_cfg.get(event))):
            changed += 1

    fd, tmp = tempfile.mkstemp(dir=CODEX_HOOKS_PATH.parent, prefix=".hooks.", suffix=".json")
    with os.fdopen(fd, "w") as f:
        json.dump(settings, f, indent=2)
        f.write("\n")
    os.replace(tmp, CODEX_HOOKS_PATH)

    print(f"  wrote   -> {CODEX_HOOKS_PATH} ({changed} event(s) updated)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
