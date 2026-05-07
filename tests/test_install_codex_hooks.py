"""Tests for the Codex hook patcher's normalize/upsert primitives."""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import install_codex_hooks as ins  # noqa: E402


def test_make_command_includes_source_codex():
    cmd = ins.make_command("waiting")
    assert "--source=codex" in cmd
    assert "waiting" in cmd


def test_is_ours_requires_source_codex():
    assert ins.is_ours(ins.make_command("asking"))
    # Claude-style command (no --source=codex) is NOT ours under this patcher.
    assert not ins.is_ours(
        '"/usr/bin/python3" "/Users/x/.claude/scripts/agent-status-hook.py" asking'
    )
    # Some other tool's command, completely unrelated.
    assert not ins.is_ours("/bin/some-other-tool")


def test_normalize_routes_string():
    assert ins.normalize_routes("working") == [("", "working")]


def test_normalize_routes_dict_filters_blanks():
    routes = ins.normalize_routes({"a": "asking", "b": None, "c": ""})
    assert routes == [("a", "asking")]


def test_upsert_event_adds_new_route():
    hooks: dict = {}
    ins.upsert_event(hooks, "Stop", [("", "waiting")])
    assert hooks["Stop"][0]["matcher"] == ""
    assert ins.make_command("waiting") in [
        h["command"] for h in hooks["Stop"][0]["hooks"]
    ]


def test_upsert_event_preserves_other_tools():
    other = "/bin/some-other-tool"
    hooks = {
        "PreToolUse": [{"matcher": "", "hooks": [{"type": "command", "command": other}]}],
    }
    ins.upsert_event(hooks, "PreToolUse", [("", "working")])
    cmds = [h["command"] for h in hooks["PreToolUse"][0]["hooks"]]
    assert other in cmds
    assert ins.make_command("working") in cmds


def test_upsert_event_replaces_stale_codex_route():
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


def test_upsert_event_unwires_when_empty():
    hooks = {
        "Stop": [{
            "matcher": "",
            "hooks": [{"type": "command", "command": ins.make_command("waiting")}],
        }],
    }
    ins.upsert_event(hooks, "Stop", [])
    assert "Stop" not in hooks
