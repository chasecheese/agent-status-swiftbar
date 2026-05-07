# claude-code-swiftbar

A tiny [SwiftBar](https://swiftbar.app) plugin that turns the macOS menu bar into a status light for [Claude Code](https://claude.com/claude-code) sessions.

The icon shows the most-urgent state across all your active Claude Code windows. Click any session row in the dropdown to jump back to its terminal/IDE window.

## States

Pure SF Symbols — no color — so the menu bar adapts to light/dark automatically. Defaults:

| Default symbol                       | State     | Means                                                            |
| ------------------------------------ | --------- | ---------------------------------------------------------------- |
| `exclamationmark.bubble.circle.fill` | `asking`  | Claude is blocked on permission / user input — **you must act**  |
| `hourglass.circle.fill`              | `working` | Claude is running                                                |
| `circle.badge.checkmark`             | `waiting` | Fresh session, or Claude finished a turn — awaiting your prompt  |
| `circle.dotted`                      | `none`    | No active sessions (menu-bar header only)                        |

All icons / priority / hook routing are editable in `~/.claude/swiftbar-config.json` — see [Configuration](#configuration).

## Why

If a Claude Code turn takes more than a few seconds you tab away. The menu bar tells you when to come back — without flipping windows.

## Requirements

- macOS
- [SwiftBar](https://swiftbar.app) (`brew install --cask swiftbar`)
- [Claude Code](https://claude.com/claude-code)
- `/usr/bin/python3` (system Python; ships with macOS)

## Install

```bash
git clone https://github.com/<you>/claude-code-swiftbar
cd claude-code-swiftbar
./install.sh
```

The installer is idempotent and lays files out like this:

```text
~/.claude/scripts/
  ├── claudebar.py                       shared lib (paths, defaults, helpers)
  ├── claude-swiftbar-hook.py            hook entry — writes per-session state
  └── claude-swiftbar-plugin.py          plugin entry — renders dropdown
~/.claude/swiftbar-config.json           user config (preserved across installs)
~/.claude/settings.json                  patched: our hook routes wired in (backed up)
<SwiftBar plugins>/claude-status.<N>.sh  thin bash wrapper SwiftBar runs
```

`SWIFTBAR_PLUGIN_DIR=/path ./install.sh` overrides plugin folder detection.

Other tools' hooks on the same Claude Code events are preserved.

## How it works

```text
              ┌─── hook fires (PreToolUse / Stop / Notification / ...) ───┐
Claude Code ──┤                                                           │
              │   /usr/bin/python3 claude-swiftbar-hook.py <state>        │
              │   writes ~/.claude/state/swiftbar/<session_id>.json       │
              └───────────────────────────────────────────────────────────┘
                                       │
                                       ▼
SwiftBar (every refresh_interval_ms) ──► claude-swiftbar-plugin.py
                                            • reads all state files
                                            • picks highest-priority state
                                            • emits SwiftBar markup
```

State files older than 12 hours are ignored (handles the case where Claude Code crashes and skips `SessionEnd`).

The hook also captures, once per session and persisted across writes:

- **prompt** (from `UserPromptSubmit` payload) and **summary** (Claude's auto-generated `ai-title` from the transcript) — shown in the dropdown
- **terminal_app** (parent process tree → outermost `.app/Contents/` match) and **tty** — used to focus the right window on click

## Configuration

Everything tunable lives in `~/.claude/swiftbar-config.json`:

```json
{
  "refresh_interval_ms": 500,
  "icons": {
    "asking":  "exclamationmark.bubble.circle.fill",
    "working": "hourglass.circle.fill",
    "waiting": "circle.badge.checkmark",
    "none":    "circle.dotted"
  },
  "header_icons": {
    "asking":  "exclamationmark.circle.fill",
    "working": "hourglass",
    "waiting": "checkmark.circle",
    "none":    "circle.dotted"
  },
  "notify_icons": {
    "asking":  "exclamationmark.bubble.circle.fill",
    "working": "hourglass.circle.fill",
    "waiting": "circle.badge.checkmark"
  },
  "priority": ["asking", "working", "waiting"],
  "notifications": {
    "enabled_states":  [],
    "sound":           false,
    "sound_name":      "Glass",
    "include_summary": true
  },
  "events": {
    "SessionStart":     "waiting",
    "UserPromptSubmit": "working",
    "PreToolUse":       "working",
    "PostToolUse":      "working",
    "Notification": {
      "permission_prompt":  "asking",
      "elicitation_dialog": "asking",
      "idle_prompt":        null,
      "auth_success":       null
    },
    "Stop":             "waiting",
    "SubagentStop":     null,
    "PreCompact":       null,
    "SessionEnd":       "end"
  }
}
```

- **`refresh_interval_ms`** — how often SwiftBar re-runs the plugin, in milliseconds. Floored at 100ms. Encoded into the plugin filename (e.g. `claude-status.500ms.sh`) by `install.sh`; this key needs a re-install to take effect.
- **`icons`** — dropdown row icons. State name → [SF Symbol](https://developer.apple.com/sf-symbols/) name.
- **`header_icons`** — menu-bar icons. Optional; falls back per-state to `icons` when missing. Lets you keep the menu bar simple and the dropdown more descriptive.
- **`notify_icons`** — icons used in each session row's `Notify on:` toggle list. Optional; falls back per-state to `icons`.
- **`priority`** — ordered list used to pick the header icon when multiple sessions are active. Earlier = higher priority. `none` is the implicit fallback when no record matches.
- **`notifications`** — macOS desktop notification defaults (per-session opt-in via the dropdown toggles, which write `notify_states` into the session's state file):
  - `enabled_states` — global default list (used when the session has no override yet).
  - `sound` / `sound_name` — play a system sound (default off; Glass when on).
  - `include_summary` — whether to put the session's `ai-title` in the notification body.
- **`events`** — every official Claude Code [hook event](https://code.claude.com/docs/en/hooks.md). Each entry can be:
  - a **string** state name → single `matcher=""` route;
  - a **dict** of `matcher → state` → per-subtype routing (e.g. `Notification`, or per-tool `PreToolUse`);
  - `null` / `""` → unwired (any prior wiring of ours is removed on next install).

Entries that name a state outside the known vocabulary (`asking` / `working` / `waiting` / `none`) are silently dropped, so leaving stale state names in your config is harmless.

After editing, run `./install.sh` again. Icons and priority are re-read on every tick (changes apply within one refresh cycle); `events` and `refresh_interval_ms` are baked into `~/.claude/settings.json` and the plugin filename respectively, so they need a re-install.

## Click action: "jump to window"

Clicking a session row in the dropdown brings the originating environment back to focus:

| Host                            | Click behaviour                                          |
| ------------------------------- | -------------------------------------------------------- |
| Terminal.app                    | per-tab focus by `tty`                                   |
| iTerm                           | per-session focus by `tty`                               |
| VS Code / Cursor / Windsurf     | `<bin> --reuse-window <cwd>` (focuses workspace window)  |
| Ghostty / Warp / Alacritty / …  | `tell application "<NAME>" to activate` (app-level)      |
| Unknown / unbundled             | falls back to opening `cwd` in Finder                    |

## Desktop notifications

Each state transition can trigger a macOS notification. **Out of the box no state is enabled** — opt in per-session via the dropdown toggles described below. Body shows the session's `ai-title`; sound is off by default.

**Banner delivery**: macOS routes `osascript display notification` through the *Script Editor* host bundle, whose default style is often "None" (notifications go straight to Notification Center, no banner). Two ways to get a real banner:

- One-time fix: System Settings → Notifications → Script Editor → set Alert style to **Banners** or **Alerts**.
- Or `brew install terminal-notifier` — when present, the hook uses it instead of osascript. You grant banner permission to its own bundle ID, *and* clicking the banner jumps you back to the originating Terminal/iTerm tab (by `tty`) or VS Code/Cursor workspace window (by `--reuse-window <cwd>`); osascript notifications can't do click-to-focus.

The dropdown's per-session submenu lets you toggle which states fire for each session (writes to that session's state file, so two sessions in the same project stay independent):

```text
ASKING   running migration   (12s ago)
└ in my-project
└ Open folder
└ ─────
└ Notify on:
└ ✓ ASKING
└   WORKING
└ ✓ WAITING
```

Clicks write `notify_states` into the session's state file. Each session is independent; toggling one doesn't affect others (even in the same project). The override is gone when the session ends.

## Repo layout

```text
.
├── install.sh / uninstall.sh
├── lib/claudebar.py              shared utilities (single source of truth)
├── hook/claude-swiftbar-hook.py  thin hook entry
├── plugin/
│   ├── claude-status.sh          SwiftBar wrapper (metadata + exec python entry)
│   ├── claude-status.py          plugin entry (renders dropdown)
│   ├── toggle.py                 dropdown click callback (toggles notification prefs)
│   └── swiftbar-config.json      seed config (deployed to ~/.claude/)
├── scripts/install_settings.py   patches ~/.claude/settings.json
├── scripts/uninstall_settings.py reverses the patcher
└── tests/                        pytest suite for the pure logic in claudebar.py
```

## Development

The repo uses [uv](https://github.com/astral-sh/uv) to manage a dev virtualenv (only for the test runner — runtime code is stdlib-only and runs under the system `/usr/bin/python3` after install).

```bash
# one-time setup: creates .venv/ and installs pytest
uv sync --group dev

# run the test suite
uv run pytest

# run a single test file
uv run pytest tests/test_claudebar.py -v
```

The dependency surface is intentionally tiny:

- `pyproject.toml` declares `pytest` in the `[dependency-groups].dev` group
- `[tool.pytest.ini_options].pythonpath = ["lib"]` lets tests import `claudebar` directly
- `[tool.uv].package = false` keeps `uv sync` from trying to build a wheel
- No production dependencies — everything the deployed scripts need ships with macOS

Test coverage spans config parsing (defaults, vocabulary filtering, edge cases), state aggregation, transcript tail parsing, plugin filename calculation, the `settings.json` patcher's normalize/upsert primitives, the per-session notification toggle, and the click-command shell builder. ~65 cases, all run in under 100 ms.

## Uninstall

```bash
./uninstall.sh
```

Removes the plugin wrapper, the three deployed Python files in `~/.claude/scripts/`, and our entries in `settings.json`. Config and state files are left in place; the script prints the commands to wipe them if you want.

## Related projects

- [gmr/claude-status](https://github.com/gmr/claude-status) — full menu-bar app with desktop widget; same idea, much more featureful.
- [VibeStatus](https://www.vibestatus.app) — closed-source paid menu bar app with push notifications.
- [PiXeL16/claudecode-macmenu](https://github.com/PiXeL16/claudecode-macmenu) — menu bar app with usage analytics.

This project is the "small SwiftBar plugin you can hack on" version of the same idea.

## License

MIT — see [LICENSE](LICENSE).
