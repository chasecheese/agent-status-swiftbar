"""Unit tests for scripts/install_claude_hooks.py.

Exercises the settings.json patcher's normalize/upsert primitives so we
catch regressions in the (most subtle, most error-prone) part of the
codebase without needing to actually run install.sh.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import install_claude_hooks as ins  # noqa: E402


def test_normalize_routes_string_becomes_default_matcher():
    assert ins.normalize_routes("idle") == [("", "idle")]


def test_normalize_routes_dict_keeps_per_matcher_pairs():
    routes = ins.normalize_routes({
        "permission_prompt": "asking",
        "auth_success": "notify",
        "idle_prompt": None,         # null → dropped
        "blank": "",                 # empty string → dropped
    })
    assert sorted(routes) == [
        ("auth_success", "notify"),
        ("permission_prompt", "asking"),
    ]


def test_normalize_routes_null_means_unwired():
    assert ins.normalize_routes(None) == []
    assert ins.normalize_routes("") == []


def test_upsert_event_adds_route_to_empty_event():
    hooks: dict = {}
    changed = ins.upsert_event(hooks, "Stop", [("", "waiting")])
    assert changed
    assert hooks["Stop"] == [{
        "matcher": "",
        "hooks": [{"type": "command", "command": ins.make_command("waiting")}],
    }]


def test_upsert_event_preserves_other_tools_hooks():
    other_cmd = "/bin/some-other-tool"
    hooks = {
        "Stop": [{
            "matcher": "",
            "hooks": [{"type": "command", "command": other_cmd}],
        }],
    }
    ins.upsert_event(hooks, "Stop", [("", "waiting")])
    cmds = [h["command"] for h in hooks["Stop"][0]["hooks"]]
    assert other_cmd in cmds
    assert ins.make_command("waiting") in cmds


def test_upsert_event_replaces_stale_route():
    hooks = {
        "Stop": [{
            "matcher": "",
            "hooks": [{"type": "command", "command": ins.make_command("done")}],
        }],
    }
    ins.upsert_event(hooks, "Stop", [("", "waiting")])
    cmds = [h["command"] for h in hooks["Stop"][0]["hooks"]]
    assert ins.make_command("waiting") in cmds
    assert ins.make_command("done") not in cmds


def test_upsert_event_unwires_when_routes_empty():
    hooks = {
        "Stop": [{
            "matcher": "",
            "hooks": [{"type": "command", "command": ins.make_command("waiting")}],
        }],
    }
    ins.upsert_event(hooks, "Stop", [])
    # Whole event entry removed since no commands remained.
    assert "Stop" not in hooks


def test_upsert_event_per_matcher_routes_for_notification():
    hooks: dict = {}
    ins.upsert_event(hooks, "Notification", [
        ("permission_prompt", "asking"),
        ("auth_success", "notify"),
    ])
    matchers = sorted(e["matcher"] for e in hooks["Notification"])
    assert matchers == ["auth_success", "permission_prompt"]
