"""Tests for the notifications config + the toggle helper.

Per-session notification overrides live in each session's state file
(``notify_states`` field), so the toggle script has to read/write the
state file rather than mutating the global config. These tests pin
that contract.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "lib"))
sys.path.insert(0, str(REPO_ROOT / "plugin"))

import agentstatus  # noqa: E402

_toggle_spec = importlib.util.spec_from_file_location(
    "claude_swiftbar_toggle", REPO_ROOT / "plugin" / "toggle.py",
)
toggle = importlib.util.module_from_spec(_toggle_spec)
_toggle_spec.loader.exec_module(toggle)


# ── Config defaults / merging ────────────────────────────────────────────────
def test_load_config_defaults_for_notifications(tmp_path):
    cfg = agentstatus.load_config(tmp_path / "missing.json")
    assert cfg["notifications"] == agentstatus.DEFAULT_NOTIFICATIONS


def test_load_config_user_notifications_override(tmp_path):
    p = tmp_path / "swiftbar-config.json"
    p.write_text(json.dumps({
        "notifications": {
            "enabled_states": ["waiting"],
            "sound": True,
            "sound_name": "Submarine",
        }
    }))
    n = agentstatus.load_config(p)["notifications"]
    assert n["enabled_states"] == ["waiting"]
    assert n["sound"] is True
    assert n["sound_name"] == "Submarine"
    assert n["include_summary"] is True


def test_load_config_filters_unknown_states_in_enabled(tmp_path):
    p = tmp_path / "swiftbar-config.json"
    p.write_text(json.dumps({
        "notifications": {"enabled_states": ["asking", "bogus", 42]}
    }))
    n = agentstatus.load_config(p)["notifications"]
    assert n["enabled_states"] == ["asking"]


# ── Per-session override resolution ──────────────────────────────────────────
def test_effective_states_uses_record_override():
    record = {"notify_states": ["waiting"]}
    notif = {"enabled_states": ["asking"]}
    assert agentstatus.effective_enabled_states(record, notif) == ["waiting"]


def test_effective_states_explicit_empty_silences_session():
    record = {"notify_states": []}
    notif = {"enabled_states": ["asking"]}
    assert agentstatus.effective_enabled_states(record, notif) == []


def test_effective_states_falls_back_to_global_when_no_override():
    record = {}  # no notify_states key
    notif = {"enabled_states": ["asking", "idle"]}
    assert agentstatus.effective_enabled_states(record, notif) == ["asking", "idle"]


def test_effective_states_ignores_non_list_override():
    record = {"notify_states": None}
    notif = {"enabled_states": ["asking"]}
    assert agentstatus.effective_enabled_states(record, notif) == ["asking"]


# ── maybe_notify gating ──────────────────────────────────────────────────────
def test_maybe_notify_skips_when_state_unchanged(monkeypatch):
    calls = []
    monkeypatch.setattr(agentstatus.subprocess, "run",
                        lambda *a, **kw: calls.append((a, kw)))
    agentstatus.maybe_notify("asking", "asking", "x", "/tmp", {},
                           {"enabled_states": ["asking"]})
    assert calls == []


def test_maybe_notify_skips_when_state_not_in_effective(monkeypatch):
    calls = []
    monkeypatch.setattr(agentstatus.subprocess, "run",
                        lambda *a, **kw: calls.append((a, kw)))
    agentstatus.maybe_notify("working", "idle", "x", "/tmp", {},
                           {"enabled_states": ["asking"]})
    assert calls == []


def test_maybe_notify_fires_on_enabled_transition(monkeypatch):
    calls = []
    monkeypatch.setattr(agentstatus.shutil, "which", lambda name: None)
    monkeypatch.setattr(agentstatus.subprocess, "run",
                        lambda *a, **kw: calls.append((a, kw)))
    agentstatus.maybe_notify(
        "asking", "working", "my project", "/tmp", {},
        {"enabled_states": ["asking"], "include_summary": True},
    )
    assert len(calls) == 1
    script = calls[0][0][0][2]
    assert "display notification" in script
    assert "ASKING" in script
    assert "my project" in script


def test_maybe_notify_session_override_takes_precedence(monkeypatch):
    """Session has notify_states=[] → silenced even if global says asking."""
    calls = []
    monkeypatch.setattr(agentstatus.subprocess, "run",
                        lambda *a, **kw: calls.append((a, kw)))
    agentstatus.maybe_notify(
        "asking", "working", "x", "/tmp",
        {"notify_states": []},
        {"enabled_states": ["asking"]},
    )
    assert calls == []


def test_maybe_notify_includes_sound_when_requested(monkeypatch):
    calls = []
    monkeypatch.setattr(agentstatus.shutil, "which", lambda name: None)
    monkeypatch.setattr(agentstatus.subprocess, "run",
                        lambda *a, **kw: calls.append((a, kw)))
    agentstatus.maybe_notify(
        "asking", "working", "x", "", {},
        {"enabled_states": ["asking"], "sound": True, "sound_name": "Glass"},
    )
    script = calls[0][0][0][2]
    assert 'sound name "Glass"' in script


def test_click_command_shell_terminal_uses_tty(tmp_path):
    cmd = agentstatus.click_command_shell("Terminal", "/dev/ttys015", "/tmp/foo")
    assert cmd.startswith(agentstatus.OSASCRIPT)
    assert "/dev/ttys015" in cmd
    assert 'tell application "Terminal"' in cmd


def test_click_command_shell_iterm_uses_tty():
    cmd = agentstatus.click_command_shell("iTerm", "/dev/ttys020", "/tmp/foo")
    assert "tty of s is" in cmd
    assert "/dev/ttys020" in cmd


def test_click_command_shell_vscode_uses_reuse_window(monkeypatch):
    bin_path = agentstatus.IDE_BIN["Visual Studio Code"]
    monkeypatch.setattr(agentstatus.Path, "exists", lambda self: True)
    cmd = agentstatus.click_command_shell("Visual Studio Code", "", "/projects/foo")
    assert "--reuse-window" in cmd
    assert "/projects/foo" in cmd
    assert bin_path in cmd


def test_click_command_shell_unknown_app_falls_back_to_activate():
    cmd = agentstatus.click_command_shell("Ghostty", "", "/tmp")
    assert 'tell application "Ghostty" to activate' in cmd


def test_click_command_shell_empty_when_nothing_known():
    assert agentstatus.click_command_shell("", "", "") == ""


def test_maybe_notify_passes_execute_when_terminal_notifier_present(monkeypatch):
    """terminal-notifier path must include -execute with the focus command."""
    calls = []
    monkeypatch.setattr(agentstatus.shutil, "which",
                        lambda name: "/usr/local/bin/terminal-notifier" if name == "terminal-notifier" else None)
    monkeypatch.setattr(agentstatus.subprocess, "run",
                        lambda *a, **kw: calls.append((a, kw)))
    record = {"terminal_app": "Terminal", "tty": "/dev/ttys001"}
    agentstatus.maybe_notify("asking", "working", "x", "/tmp", record,
                           {"enabled_states": ["asking"]})
    assert len(calls) == 1
    argv = calls[0][0][0]
    assert argv[0] == "/usr/local/bin/terminal-notifier"
    # -execute appears with a non-empty value referencing the tty
    assert "-execute" in argv
    exec_idx = argv.index("-execute")
    assert "/dev/ttys001" in argv[exec_idx + 1]


def test_maybe_notify_skips_execute_when_no_terminal_app(monkeypatch):
    calls = []
    monkeypatch.setattr(agentstatus.shutil, "which",
                        lambda name: "/usr/local/bin/terminal-notifier" if name == "terminal-notifier" else None)
    monkeypatch.setattr(agentstatus.subprocess, "run",
                        lambda *a, **kw: calls.append((a, kw)))
    record = {"terminal_app": "", "tty": ""}
    agentstatus.maybe_notify("asking", "working", "x", "/tmp", record,
                           {"enabled_states": ["asking"]})
    argv = calls[0][0][0]
    assert "-execute" not in argv


def test_maybe_notify_escapes_quotes_in_summary(monkeypatch):
    calls = []
    monkeypatch.setattr(agentstatus.shutil, "which", lambda name: None)
    monkeypatch.setattr(agentstatus.subprocess, "run",
                        lambda *a, **kw: calls.append((a, kw)))
    agentstatus.maybe_notify(
        "asking", "working", 'said "hello" then', "", {},
        {"enabled_states": ["asking"]},
    )
    script = calls[0][0][0][2]
    assert 'said \\"hello\\" then' in script


# ── Toggle script ────────────────────────────────────────────────────────────
def _patch_paths(monkeypatch, tmp_path):
    cfg_path = tmp_path / "swiftbar-config.json"
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    monkeypatch.setattr(agentstatus, "CONFIG_PATH", cfg_path)
    monkeypatch.setattr(agentstatus, "STATE_DIR", state_dir)
    monkeypatch.setattr(toggle, "CONFIG_PATH", cfg_path)
    monkeypatch.setattr(toggle, "STATE_DIR", state_dir)
    return cfg_path, state_dir


def _write_state(state_dir, sid: str, body: dict) -> Path:
    p = state_dir / f"{sid}.json"
    p.write_text(json.dumps(body))
    return p


def test_toggle_session_seeds_from_global_default_then_flips(tmp_path, monkeypatch):
    cfg_path, state_dir = _patch_paths(monkeypatch, tmp_path)
    cfg_path.write_text(json.dumps({"notifications": {"enabled_states": ["asking"]}}))
    state_path = _write_state(state_dir, "sid-A", {
        "state": "working", "session_id": "sid-A",
    })
    monkeypatch.setattr(sys, "argv", ["toggle.py", "--session", "sid-A", "--state", "waiting"])
    toggle.main()
    saved = json.loads(state_path.read_text())
    # Started from global ["asking"] → added "waiting"
    assert sorted(saved["notify_states"]) == ["asking", "waiting"]


def test_toggle_session_removes_when_present(tmp_path, monkeypatch):
    cfg_path, state_dir = _patch_paths(monkeypatch, tmp_path)
    cfg_path.write_text(json.dumps({"notifications": {"enabled_states": ["asking"]}}))
    state_path = _write_state(state_dir, "sid-A", {
        "state": "working", "session_id": "sid-A",
        "notify_states": ["asking", "waiting"],
    })
    monkeypatch.setattr(sys, "argv", ["toggle.py", "--session", "sid-A", "--state", "asking"])
    toggle.main()
    assert json.loads(state_path.read_text())["notify_states"] == ["waiting"]


def test_toggle_session_isolated_from_other_sessions(tmp_path, monkeypatch):
    """Two sessions in the same cwd; toggling one must not affect the other."""
    cfg_path, state_dir = _patch_paths(monkeypatch, tmp_path)
    cfg_path.write_text(json.dumps({"notifications": {"enabled_states": ["asking"]}}))
    a = _write_state(state_dir, "sid-A", {
        "state": "working", "session_id": "sid-A", "cwd": "/projects/foo",
    })
    b = _write_state(state_dir, "sid-B", {
        "state": "working", "session_id": "sid-B", "cwd": "/projects/foo",
    })
    monkeypatch.setattr(sys, "argv", ["toggle.py", "--session", "sid-A", "--state", "asking"])
    toggle.main()
    a_after = json.loads(a.read_text())
    b_after = json.loads(b.read_text())
    assert a_after.get("notify_states") == []
    # B was never touched — no notify_states added.
    assert "notify_states" not in b_after


def test_toggle_session_silently_noops_when_state_file_missing(tmp_path, monkeypatch):
    _patch_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(sys, "argv", ["toggle.py", "--session", "ghost", "--state", "asking"])
    # Should not raise.
    toggle.main()


def test_toggle_sound_flips_global(tmp_path, monkeypatch):
    cfg_path, _ = _patch_paths(monkeypatch, tmp_path)
    cfg_path.write_text(json.dumps({"notifications": {"sound": False}}))
    monkeypatch.setattr(sys, "argv", ["toggle.py", "--sound"])
    toggle.main()
    assert json.loads(cfg_path.read_text())["notifications"]["sound"] is True
