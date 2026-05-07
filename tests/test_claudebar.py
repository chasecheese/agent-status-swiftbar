"""Unit tests for the lib/claudebar.py shared module.

These exercise the pure logic — config parsing, state aggregation,
filename calculation, transcript tail parsing — without touching the
real ~/.claude paths. They run under any Python 3.10+ via pytest.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

import claudebar


# ── Config loading ───────────────────────────────────────────────────────────
def _write_cfg(tmp_path: Path, body: dict) -> Path:
    p = tmp_path / "swiftbar-config.json"
    p.write_text(json.dumps(body))
    return p


def test_load_config_missing_returns_defaults(tmp_path):
    cfg = claudebar.load_config(tmp_path / "nope.json")
    assert cfg["icons"] == claudebar.DEFAULT_ICONS
    assert cfg["priority"] == claudebar.DEFAULT_PRIORITY
    assert cfg["claude_events"] == claudebar.DEFAULT_EVENTS
    assert cfg["refresh_interval_ms"] == claudebar.DEFAULT_REFRESH_INTERVAL_MS


def test_load_config_merges_user_icons(tmp_path):
    p = _write_cfg(tmp_path, {"icons": {"asking": "star.fill"}})
    cfg = claudebar.load_config(p)
    assert cfg["icons"]["asking"] == "star.fill"
    # Other defaults preserved
    assert cfg["icons"]["working"] == claudebar.DEFAULT_ICONS["working"]


def test_load_config_priority_filters_unknown_states(tmp_path):
    # `none` is reserved as the implicit fallback; priority list filters it out.
    p = _write_cfg(tmp_path, {"priority": ["asking", "bogus", "none", "working"]})
    cfg = claudebar.load_config(p)
    assert cfg["priority"] == ["asking", "working"]


def test_load_config_priority_falls_back_when_all_invalid(tmp_path):
    p = _write_cfg(tmp_path, {"priority": ["bogus", "none"]})
    cfg = claudebar.load_config(p)
    assert cfg["priority"] == claudebar.DEFAULT_PRIORITY


def test_load_config_clamps_refresh_interval(tmp_path):
    p = _write_cfg(tmp_path, {"refresh_interval_ms": 50})
    assert claudebar.load_config(p)["refresh_interval_ms"] == claudebar.MIN_REFRESH_INTERVAL_MS


def test_load_config_handles_garbage_refresh_interval(tmp_path):
    p = _write_cfg(tmp_path, {"refresh_interval_ms": "not a number"})
    assert claudebar.load_config(p)["refresh_interval_ms"] == claudebar.DEFAULT_REFRESH_INTERVAL_MS


def test_load_config_returns_defaults_when_file_missing(tmp_path):
    cfg = claudebar.load_config(tmp_path / "missing.json")
    assert cfg["icons"] == claudebar.DEFAULT_ICONS
    assert cfg["priority"] == claudebar.DEFAULT_PRIORITY
    assert cfg["claude_events"] == claudebar.DEFAULT_EVENTS
    assert cfg["notifications"] == claudebar.DEFAULT_NOTIFICATIONS
    # Default claude_events seed a fresh session straight to waiting.
    assert cfg["claude_events"]["SessionStart"] == "waiting"


def test_load_config_filters_legacy_state_names(tmp_path):
    """User config naming retired states (`idle`, `notify`) should drop
    those entries silently rather than poisoning the resolved config."""
    p = tmp_path / "swiftbar-config.json"
    p.write_text(json.dumps({
        "icons":    {"asking": "star.fill", "idle": "circle"},
        "priority": ["asking", "idle", "notify", "working"],
        "notifications": {"enabled_states": ["asking", "idle", "notify"]},
        "claude_events": {"SessionStart": "idle",
                          "Stop":         "waiting",
                          "Notification": {"permission_prompt": "asking",
                                           "auth_success":      "notify"}},
    }))
    cfg = claudebar.load_config(p)
    assert "idle" not in cfg["icons"]
    assert cfg["priority"] == ["asking", "working"]
    assert cfg["notifications"]["enabled_states"] == ["asking"]
    # SessionStart fell back to the default ("waiting") since the user named idle
    assert cfg["claude_events"]["SessionStart"] == "waiting"
    # Notification kept the legitimate matcher, dropped the auth_success → notify
    nroutes = cfg["claude_events"]["Notification"]
    assert nroutes.get("permission_prompt") == "asking"
    assert "auth_success" not in nroutes


def test_load_config_falls_back_to_legacy_events_key(tmp_path):
    """Pre-rename configs used `events` as the Claude-events key. Treat
    it as a legacy alias so old configs keep working without manual edits."""
    p = tmp_path / "swiftbar-config.json"
    p.write_text(json.dumps({
        "events": {"SessionStart": "asking", "Stop": "waiting"},
    }))
    cfg = claudebar.load_config(p)
    assert cfg["claude_events"]["SessionStart"] == "asking"
    assert cfg["claude_events"]["Stop"] == "waiting"


def test_header_icons_default_to_row_icons(tmp_path):
    """When the user doesn't set header_icons, it mirrors `icons`."""
    cfg = claudebar.load_config(tmp_path / "missing.json")
    assert cfg["header_icons"] == cfg["icons"]


def test_header_icons_override_specific_states(tmp_path):
    p = tmp_path / "swiftbar-config.json"
    p.write_text(json.dumps({
        "icons":        {"asking": "exclamationmark.bubble.circle.fill"},
        "header_icons": {"asking": "exclamationmark.circle.fill"},
    }))
    cfg = claudebar.load_config(p)
    # Row icons untouched
    assert cfg["icons"]["asking"] == "exclamationmark.bubble.circle.fill"
    # Header icon overridden
    assert cfg["header_icons"]["asking"] == "exclamationmark.circle.fill"
    # States the user didn't override fall back to row icons
    assert cfg["header_icons"]["working"] == cfg["icons"]["working"]


def test_notify_icons_default_to_row_icons(tmp_path):
    cfg = claudebar.load_config(tmp_path / "missing.json")
    assert cfg["notify_icons"] == cfg["icons"]


def test_notify_icons_override_specific_states(tmp_path):
    p = tmp_path / "swiftbar-config.json"
    p.write_text(json.dumps({
        "icons":         {"asking": "exclamationmark.bubble.circle.fill"},
        "notify_icons":  {"asking": "bell.circle.fill"},
    }))
    cfg = claudebar.load_config(p)
    assert cfg["icons"]["asking"] == "exclamationmark.bubble.circle.fill"
    assert cfg["notify_icons"]["asking"] == "bell.circle.fill"
    # Header icons stay aligned with row icons (untouched here)
    assert cfg["header_icons"]["asking"] == cfg["icons"]["asking"]


def test_notify_icons_filtered_against_vocabulary(tmp_path):
    p = tmp_path / "swiftbar-config.json"
    p.write_text(json.dumps({
        "notify_icons": {
            "asking":  "bell",
            "notify":  "bell.circle.fill",  # legacy state name → dropped
            "working": "hourglass.circle",
        },
    }))
    cfg = claudebar.load_config(p)
    assert "notify" not in cfg["notify_icons"]
    assert cfg["notify_icons"]["asking"] == "bell"
    assert cfg["notify_icons"]["working"] == "hourglass.circle"


def test_header_icons_filtered_against_vocabulary(tmp_path):
    p = tmp_path / "swiftbar-config.json"
    p.write_text(json.dumps({
        "header_icons": {
            "asking":  "exclamationmark.circle.fill",
            "notify":  "bell",        # legacy → dropped
            "working": "hourglass",
        },
    }))
    cfg = claudebar.load_config(p)
    assert "notify" not in cfg["header_icons"]
    assert cfg["header_icons"]["asking"] == "exclamationmark.circle.fill"
    assert cfg["header_icons"]["working"] == "hourglass"


def test_codex_events_default_when_missing(tmp_path):
    cfg = claudebar.load_config(tmp_path / "missing.json")
    assert cfg["codex_events"] == claudebar.DEFAULT_CODEX_EVENTS
    # PermissionRequest is the Codex equivalent of Notification.permission_prompt
    assert cfg["codex_events"]["PermissionRequest"] == "asking"


def test_codex_events_user_override(tmp_path):
    p = tmp_path / "swiftbar-config.json"
    p.write_text(json.dumps({
        "codex_events": {
            "PermissionRequest": "asking",
            "PreToolUse":        "working",
            "Stop":              "waiting",
            # Bogus state name — should be filtered out by _filter_events,
            # leaving the default in place.
            "SessionStart":      "fictional_state",
        }
    }))
    cfg = claudebar.load_config(p)
    # Valid keys merged
    assert cfg["codex_events"]["PermissionRequest"] == "asking"
    # Invalid state for SessionStart drops back to default ("waiting")
    assert cfg["codex_events"]["SessionStart"] == "waiting"


def test_action_icons_default_when_missing(tmp_path):
    cfg = claudebar.load_config(tmp_path / "missing.json")
    assert cfg["action_icons"] == claudebar.DEFAULT_ACTION_ICONS


def test_action_icons_user_override_known_keys(tmp_path):
    p = tmp_path / "swiftbar-config.json"
    p.write_text(json.dumps({
        "action_icons": {
            "open_folder":   "folder.fill",
            "return_to_tab": "arrow.uturn.left",
            "unknown_key":   "should-be-ignored",
        }
    }))
    cfg = claudebar.load_config(p)
    assert cfg["action_icons"]["open_folder"] == "folder.fill"
    assert cfg["action_icons"]["return_to_tab"] == "arrow.uturn.left"
    # Unspecified key keeps its default
    assert cfg["action_icons"]["message"] == claudebar.DEFAULT_ACTION_ICONS["message"]
    # Unknown key dropped (action_icons schema is closed)
    assert "unknown_key" not in cfg["action_icons"]


def test_default_notifications_are_off(tmp_path):
    """Out of the box no state is enabled — opt-in via the dropdown."""
    cfg = claudebar.load_config(tmp_path / "missing.json")
    assert cfg["notifications"]["enabled_states"] == []


def test_load_config_handles_corrupt_json(tmp_path):
    p = tmp_path / "swiftbar-config.json"
    p.write_text("{ this is not json")
    cfg = claudebar.load_config(p)
    assert cfg["icons"] == claudebar.DEFAULT_ICONS


# ── Plugin filename ──────────────────────────────────────────────────────────
@pytest.mark.parametrize("ms,expected", [
    (1000, "claude-status.1s.sh"),
    (2000, "claude-status.2s.sh"),
    (500, "claude-status.500ms.sh"),
    (250, "claude-status.250ms.sh"),
    (1500, "claude-status.1500ms.sh"),
    (50, "claude-status.100ms.sh"),  # clamped
])
def test_plugin_filename_for(ms, expected):
    assert claudebar.plugin_filename_for(ms) == expected


# ── State aggregation ────────────────────────────────────────────────────────
def test_aggregate_state_picks_highest_priority():
    records = [
        {"state": "working"},
        {"state": "asking"},
        {"state": "waiting"},
    ]
    assert claudebar.aggregate_state(records, claudebar.DEFAULT_PRIORITY) == "asking"


def test_aggregate_state_skips_states_not_in_priority():
    records = [{"state": "weird-custom"}]
    assert claudebar.aggregate_state(records, claudebar.DEFAULT_PRIORITY) == "none"


def test_aggregate_state_no_records_returns_none():
    assert claudebar.aggregate_state([], claudebar.DEFAULT_PRIORITY) == "none"


# ── State file reading ───────────────────────────────────────────────────────
def test_read_state_files_filters_stale(tmp_path, monkeypatch):
    monkeypatch.setattr(claudebar, "STATE_DIR", tmp_path)
    now = int(time.time())
    fresh = {"state": "working", "session_id": "fresh", "since": now - 60}
    stale = {"state": "working", "session_id": "stale", "since": now - claudebar.STALE_AGE_S - 10}
    fresh_path = tmp_path / "fresh.json"
    stale_path = tmp_path / "stale.json"
    fresh_path.write_text(json.dumps(fresh))
    stale_path.write_text(json.dumps(stale))
    records = claudebar.read_state_files()
    assert [r["session_id"] for r in records] == ["fresh"]
    # Stale file is opportunistically deleted on read.
    assert not stale_path.exists()
    assert fresh_path.exists()


def test_read_state_files_drops_records_with_dead_pid(tmp_path, monkeypatch):
    """A session whose agent process has exited shouldn't keep ghosting
    in the menu (covers Codex hard-quit / no SessionEnd cases)."""
    monkeypatch.setattr(claudebar, "STATE_DIR", tmp_path)
    now = int(time.time())
    # PID 1 (launchd) is always alive on macOS.
    alive = {"state": "working", "session_id": "alive",
             "since": now - 5, "agent_pid": 1}
    # PID 0 / non-existent — we use a very high pid that almost certainly
    # doesn't exist. (1 is launchd; 999999 is well past typical max_pid.)
    dead = {"state": "working", "session_id": "dead",
            "since": now - 5, "agent_pid": 999999}
    (tmp_path / "alive.json").write_text(json.dumps(alive))
    dead_path = tmp_path / "dead.json"
    dead_path.write_text(json.dumps(dead))
    records = claudebar.read_state_files()
    assert [r["session_id"] for r in records] == ["alive"]
    # Dead-pid record's file is also cleaned up.
    assert not dead_path.exists()


def test_read_state_files_keeps_records_without_agent_pid(tmp_path, monkeypatch):
    """Records written by older hook versions (no `agent_pid`) must still
    show up — falls back to the stale-age sweep alone."""
    monkeypatch.setattr(claudebar, "STATE_DIR", tmp_path)
    now = int(time.time())
    legacy = {"state": "working", "session_id": "legacy", "since": now - 60}
    (tmp_path / "legacy.json").write_text(json.dumps(legacy))
    sids = [r["session_id"] for r in claudebar.read_state_files()]
    assert sids == ["legacy"]


def test_read_state_files_skips_corrupt(tmp_path, monkeypatch):
    monkeypatch.setattr(claudebar, "STATE_DIR", tmp_path)
    (tmp_path / "ok.json").write_text(json.dumps({
        "state": "working", "session_id": "ok", "since": int(time.time()),
    }))
    (tmp_path / "broken.json").write_text("{ not valid json")
    sids = [r["session_id"] for r in claudebar.read_state_files()]
    assert sids == ["ok"]


def test_read_state_files_missing_dir_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(claudebar, "STATE_DIR", tmp_path / "does-not-exist")
    assert claudebar.read_state_files() == []


# ── Transcript parsing ───────────────────────────────────────────────────────
def test_latest_ai_title_returns_last_seen(tmp_path):
    p = tmp_path / "transcript.jsonl"
    lines = [
        {"type": "user"},
        {"type": "ai-title", "aiTitle": "first attempt"},
        {"type": "assistant"},
        {"type": "ai-title", "aiTitle": "refined title"},
        {"type": "user"},
    ]
    p.write_text("\n".join(json.dumps(line) for line in lines))
    assert claudebar.latest_ai_title(str(p)) == "refined title"


def test_latest_ai_title_handles_missing_file():
    assert claudebar.latest_ai_title("/no/such/file.jsonl") == ""


def test_latest_ai_title_empty_path():
    assert claudebar.latest_ai_title("") == ""


def test_latest_ai_title_no_titles_in_transcript(tmp_path):
    p = tmp_path / "t.jsonl"
    p.write_text(json.dumps({"type": "user"}) + "\n")
    assert claudebar.latest_ai_title(str(p)) == ""


def test_latest_ai_title_skips_blank_titles(tmp_path):
    p = tmp_path / "t.jsonl"
    lines = [
        {"type": "ai-title", "aiTitle": "real title"},
        {"type": "ai-title", "aiTitle": ""},
        {"type": "ai-title", "aiTitle": "   "},
    ]
    p.write_text("\n".join(json.dumps(line) for line in lines))
    assert claudebar.latest_ai_title(str(p)) == "real title"
