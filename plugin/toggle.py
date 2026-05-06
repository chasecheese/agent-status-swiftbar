#!/usr/bin/env python3
"""Toggle a notification preference.

Invoked from the SwiftBar dropdown:

  toggle.py --session <id> --state <name>   flip state for THIS session
                                            (writes notify_states in the
                                            session's state file)
  toggle.py --sound                          flip the global sound flag

Per-session toggles only ever touch one state file. Sessions in the same
working directory stay independent.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
from claudebar import (  # noqa: E402
    CONFIG_PATH, DEFAULT_NOTIFICATIONS, STATE_DIR,
)


# ── Global config (sound flag) ──────────────────────────────────────────────
def _load_config() -> dict:
    try:
        cfg = json.loads(CONFIG_PATH.read_text())
        if isinstance(cfg, dict):
            return cfg
    except Exception:
        pass
    return {}


def _save_config(cfg: dict) -> None:
    fd, tmp = tempfile.mkstemp(dir=CONFIG_PATH.parent, prefix=".cfg.", suffix=".json")
    with os.fdopen(fd, "w") as f:
        json.dump(cfg, f, indent=2)
        f.write("\n")
    os.replace(tmp, CONFIG_PATH)


def _toggle_sound() -> None:
    cfg = _load_config()
    notif = cfg.setdefault("notifications", {})
    cur_sound = notif.get("sound")
    if not isinstance(cur_sound, bool):
        cur_sound = bool(DEFAULT_NOTIFICATIONS.get("sound", False))
    notif["sound"] = not cur_sound
    _save_config(cfg)


# ── Per-session override (state file's notify_states) ───────────────────────
def _state_path(session_id: str):
    return STATE_DIR / f"{session_id}.json"


def _global_default_states() -> list[str]:
    cfg = _load_config()
    user = cfg.get("notifications") or {}
    states = user.get("enabled_states")
    if isinstance(states, list):
        return [s for s in states if isinstance(s, str)]
    return list(DEFAULT_NOTIFICATIONS["enabled_states"])


def _toggle_session_state(session_id: str, state: str) -> None:
    """Flip ``state`` membership in the session's notify_states list.

    First click on a session that's never been toggled seeds the override
    from the current global default (so the dropdown's checkbox stays in
    sync with what the user just saw), then flips the requested state.
    """
    p = _state_path(session_id)
    if not p.exists():
        return
    try:
        data = json.loads(p.read_text())
    except Exception:
        return
    cur = data.get("notify_states")
    if isinstance(cur, list):
        states = list(cur)
    else:
        states = _global_default_states()
    if state in states:
        states.remove(state)
    else:
        states.append(state)
    data["notify_states"] = states

    fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=f".{p.stem}.", suffix=".json")
    with os.fdopen(fd, "w") as f:
        json.dump(data, f)
    os.replace(tmp, p)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--session", help="session_id whose notify_states to toggle")
    p.add_argument("--state", help="state name to toggle")
    p.add_argument("--sound", action="store_true", help="flip the sound flag")
    args = p.parse_args()

    if args.state and args.session:
        _toggle_session_state(args.session, args.state)
    if args.sound:
        _toggle_sound()
    return 0


if __name__ == "__main__":
    sys.exit(main())
