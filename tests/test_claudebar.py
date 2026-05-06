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
    assert cfg["events"] == claudebar.DEFAULT_EVENTS
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


def test_load_config_full_mode_is_default(tmp_path):
    cfg = claudebar.load_config(tmp_path / "missing.json")
    assert cfg["mode"] == "full"
    assert cfg["icons"] == claudebar.DEFAULT_ICONS_FULL
    assert cfg["priority"] == claudebar.DEFAULT_PRIORITY_FULL
    assert cfg["events"] == claudebar.DEFAULT_EVENTS_FULL


def test_load_config_simple_mode_picks_simple_defaults(tmp_path):
    p = tmp_path / "swiftbar-config.json"
    p.write_text(json.dumps({"mode": "simple"}))
    cfg = claudebar.load_config(p)
    assert cfg["mode"] == "simple"
    assert cfg["icons"] == claudebar.DEFAULT_ICONS_SIMPLE
    assert cfg["priority"] == claudebar.DEFAULT_PRIORITY_SIMPLE
    assert cfg["events"]["SessionStart"] == "waiting"
    # No notify state in simple mode → idle/auth notifications silenced
    assert cfg["events"]["Notification"]["auth_success"] is None


def test_load_config_unknown_mode_falls_back_to_full(tmp_path):
    p = tmp_path / "swiftbar-config.json"
    p.write_text(json.dumps({"mode": "fancy"}))
    cfg = claudebar.load_config(p)
    assert cfg["mode"] == "full"


def test_load_config_simple_mode_notifications_default(tmp_path):
    """Simple mode's default enabled_states must not name a state that
    doesn't exist in this mode (e.g. ``idle``)."""
    p = tmp_path / "swiftbar-config.json"
    p.write_text(json.dumps({"mode": "simple"}))
    cfg = claudebar.load_config(p)
    assert cfg["notifications"] == claudebar.DEFAULT_NOTIFICATIONS_SIMPLE
    enabled = cfg["notifications"]["enabled_states"]
    # Every default-enabled state must have an icon in this mode.
    for s in enabled:
        assert s in cfg["icons"], f"{s} enabled but not in simple icons"


def test_load_config_simple_mode_filters_out_unknown_priority(tmp_path):
    p = tmp_path / "swiftbar-config.json"
    p.write_text(json.dumps({
        "mode": "simple",
        "priority": ["asking", "idle", "notify", "working"],  # idle/notify don't exist
    }))
    cfg = claudebar.load_config(p)
    assert cfg["priority"] == ["asking", "working"]


def test_load_config_simple_mode_filters_user_enabled_states(tmp_path):
    p = tmp_path / "swiftbar-config.json"
    p.write_text(json.dumps({
        "mode": "simple",
        "notifications": {"enabled_states": ["asking", "idle", "notify"]},
    }))
    cfg = claudebar.load_config(p)
    # idle/notify don't exist in simple icons → filtered out.
    assert cfg["notifications"]["enabled_states"] == ["asking"]


def test_simple_mode_strips_user_icons_outside_vocabulary(tmp_path):
    """Adding mode=simple to a config that still names full-mode states
    must drop those — the user shouldn't have to clean up their icons
    section to switch modes."""
    p = tmp_path / "swiftbar-config.json"
    p.write_text(json.dumps({
        "mode": "simple",
        "icons": {
            "asking":  "exclamationmark.bubble.circle.fill",
            "notify":  "bell.circle.fill",       # not in simple
            "working": "hourglass.circle.fill",
            "waiting": "circle.badge.checkmark",
            "idle":    "circle",                  # not in simple
            "none":    "circle.dotted",
        },
        "priority": ["asking", "notify", "working", "waiting", "idle"],
        "events": {
            "SessionStart": "idle",                      # idle not allowed
            "Stop":         "waiting",
            "Notification": {
                "permission_prompt":  "asking",
                "auth_success":       "notify",          # notify not allowed
            },
        },
    }))
    cfg = claudebar.load_config(p)
    # icons confined to simple vocabulary
    assert set(cfg["icons"]) == {"asking", "working", "waiting", "none"}
    # priority filtered
    assert cfg["priority"] == ["asking", "working", "waiting"]
    # SessionStart fell back to simple default ("waiting") since user's value
    # named a non-vocabulary state
    assert cfg["events"]["SessionStart"] == "waiting"
    # Stop kept as-is (waiting is in vocabulary)
    assert cfg["events"]["Stop"] == "waiting"
    # Notification: permission_prompt kept, auth_success dropped
    notif_routes = cfg["events"]["Notification"]
    assert notif_routes.get("permission_prompt") == "asking"
    assert "auth_success" not in notif_routes


def test_default_notifications_are_off_in_both_modes(tmp_path):
    """Out of the box no state is enabled — opt-in via the dropdown."""
    full = claudebar.load_config(tmp_path / "missing.json")
    assert full["notifications"]["enabled_states"] == []
    p = tmp_path / "simple.json"
    p.write_text(json.dumps({"mode": "simple"}))
    simple = claudebar.load_config(p)
    assert simple["notifications"]["enabled_states"] == []


def test_load_config_simple_mode_user_icons_merge(tmp_path):
    p = tmp_path / "swiftbar-config.json"
    p.write_text(json.dumps({
        "mode": "simple",
        "icons": {"asking": "exclamationmark.triangle.fill"},
    }))
    cfg = claudebar.load_config(p)
    assert cfg["icons"]["asking"] == "exclamationmark.triangle.fill"
    # Unspecified simple-mode icons preserved
    assert cfg["icons"]["working"] == claudebar.DEFAULT_ICONS_SIMPLE["working"]
    # Full-only states absent — `notify` not present in simple mode
    assert "notify" not in cfg["icons"]


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
    (tmp_path / "fresh.json").write_text(json.dumps(fresh))
    (tmp_path / "stale.json").write_text(json.dumps(stale))
    records = claudebar.read_state_files()
    assert [r["session_id"] for r in records] == ["fresh"]


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
