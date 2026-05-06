#!/usr/bin/env python3
"""Add claude-code-swiftbar hooks to ~/.claude/settings.json.

Reads ~/.claude/swiftbar-config.json (key "events") to decide which Claude
Code hook events get wired up and which state name they write. Idempotent —
hooks belonging to other tools are left alone, ours are stripped and rebuilt
on every run so config edits propagate cleanly.

Each event in the config maps to one of:
  - a string state name        -> single matcher='' route
  - a dict {matcher: state}    -> per-matcher routes (e.g. Notification)
  - null / "" / missing        -> not wired up (any prior wiring of ours
                                  for that event is stripped)
"""
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

PYTHON = "/usr/bin/python3"
HOOK_PY = str(Path.home() / ".claude" / "scripts" / "claude-swiftbar-hook.py")
CONFIG_PATH = Path.home() / ".claude" / "swiftbar-config.json"

# Full official Claude Code hook surface. We always visit each so disabling
# an event in the config (set null) actually removes our prior wiring.
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

# Fallback wiring used only when the config file is missing or unreadable.
DEFAULT_EVENTS = {
    "SessionStart":     "idle",
    "UserPromptSubmit": "working",
    "PreToolUse":       None,
    "PostToolUse":      None,
    "Notification": {
        "permission_prompt":  "asking",
        "elicitation_dialog": "asking",
        "idle_prompt":        "notify",
        "auth_success":       "notify",
    },
    "Stop":             "waiting",
    "SubagentStop":     None,
    "PreCompact":       None,
    "SessionEnd":       "end",
}


def make_command(state: str) -> str:
    return f'"{PYTHON}" "{HOOK_PY}" {state}'


def is_ours(cmd) -> bool:
    return isinstance(cmd, str) and HOOK_PY in cmd


def load_events_config():
    user = {}
    try:
        cfg = json.loads(CONFIG_PATH.read_text())
        if isinstance(cfg, dict) and isinstance(cfg.get("events"), dict):
            user = cfg["events"]
    except Exception:
        pass
    merged = dict(DEFAULT_EVENTS)
    merged.update(user)
    return merged


def normalize_routes(value):
    """Turn an event config value into a list of (matcher, state) tuples."""
    if value is None or value == "":
        return []
    if isinstance(value, str):
        return [("", value)]
    if isinstance(value, dict):
        out = []
        for matcher, state in value.items():
            if isinstance(matcher, str) and isinstance(state, str) and state:
                out.append((matcher, state))
        return out
    return []


def upsert_event(hooks, event, routes):
    """Strip any of our prior commands on this event, then install fresh routes."""
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
        cmd = make_command(state)
        target = next((e for e in entries if e.get("matcher", "") == matcher), None)
        if target is None:
            target = {"matcher": matcher, "hooks": []}
            entries.append(target)
        target.setdefault("hooks", []).append({"type": "command", "command": cmd})
        changed = True

    return changed


def main() -> int:
    settings_path = Path.home() / ".claude" / "settings.json"
    if settings_path.exists():
        backup = settings_path.with_suffix(".json.bak")
        shutil.copy2(settings_path, backup)
        print(f"  backup -> {backup}")
        try:
            settings = json.loads(settings_path.read_text())
        except Exception as e:
            print(f"ERROR: {settings_path} is not valid JSON: {e}", file=sys.stderr)
            return 1
    else:
        settings = {}

    events_cfg = load_events_config()
    hooks = settings.setdefault("hooks", {})
    changed = 0

    for event in ALL_EVENTS:
        routes = normalize_routes(events_cfg.get(event))
        if upsert_event(hooks, event, routes):
            changed += 1

    fd, tmp = tempfile.mkstemp(dir=settings_path.parent, prefix=".settings.", suffix=".json")
    with os.fdopen(fd, "w") as f:
        json.dump(settings, f, indent=2)
        f.write("\n")
    os.replace(tmp, settings_path)

    print(f"  wrote   -> {settings_path} ({changed} event(s) updated)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
