# claude-code-swiftbar

A tiny [SwiftBar](https://swiftbar.app) plugin that turns the macOS menu bar into a status light for [Claude Code](https://claude.com/claude-code) sessions.

The icon shows the most-urgent state across all your active Claude Code windows. Click any session row in the dropdown to jump back to its terminal/IDE window.

## States

Pure SF Symbols — no color — so the menu bar adapts to light/dark automatically. Defaults:

| Default symbol                        | State     | Means                                                              |
| ------------------------------------- | --------- | ------------------------------------------------------------------ |
| `exclamationmark.bubble.circle.fill`  | `asking`  | Claude is blocked on permission / user input — **you must act**    |
| `bell.circle.fill`                    | `notify`  | Passive ping (auth success, etc.)                                  |
| `hourglass.circle.fill`               | `working` | Claude is running                                                  |
| `circle.badge.checkmark`              | `waiting` | Claude finished its turn, awaiting your next prompt                |
| `circle`                              | `idle`    | Session open, hasn't received any prompt yet                       |
| `circle.dotted`                       | `none`    | No active sessions                                                 |

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

```
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

```
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
    "notify":  "bell.circle.fill",
    "working": "hourglass.circle.fill",
    "waiting": "circle.badge.checkmark",
    "idle":    "circle",
    "none":    "circle.dotted"
  },
  "priority": ["asking", "notify", "working", "waiting", "idle"],
  "events": {
    "SessionStart":     "idle",
    "UserPromptSubmit": "working",
    "PreToolUse":       "working",
    "PostToolUse":      "working",
    "Notification": {
      "permission_prompt":  "asking",
      "elicitation_dialog": "asking",
      "idle_prompt":        null,
      "auth_success":       "notify"
    },
    "Stop":             "waiting",
    "SubagentStop":     null,
    "PreCompact":       null,
    "SessionEnd":       "end"
  }
}
```

- **`refresh_interval_ms`** — how often SwiftBar re-runs the plugin, in milliseconds. Floored at 100ms. Encoded into the plugin filename (e.g. `claude-status.500ms.sh`) by `install.sh`; this key needs a re-install to take effect.
- **`icons`** — state name → [SF Symbol](https://developer.apple.com/sf-symbols/) name.
- **`priority`** — ordered list used to pick the header icon when multiple sessions are active. Earlier = higher priority. `none` is the implicit fallback.
- **`events`** — every official Claude Code [hook event](https://code.claude.com/docs/en/hooks.md). Each entry can be:
  - a **string** state name → single `matcher=""` route;
  - a **dict** of `matcher → state` → per-subtype routing (e.g. `Notification`, or per-tool `PreToolUse`);
  - `null` / `""` → unwired (any prior wiring of ours is removed on next install).

You can introduce custom state names — add them to `icons`, slot them into `priority`, point an event at them, then re-install.

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

## Repo layout

```
.
├── install.sh / uninstall.sh
├── lib/claudebar.py              shared utilities (single source of truth)
├── hook/claude-swiftbar-hook.py  thin hook entry
├── plugin/
│   ├── claude-status.sh          SwiftBar wrapper (metadata + exec python entry)
│   ├── claude-status.py          plugin entry (renders dropdown)
│   └── swiftbar-config.json      seed config
├── scripts/install_settings.py   patches ~/.claude/settings.json
├── scripts/uninstall_settings.py reverses the patcher
└── tests/                        pytest suite for the pure logic in claudebar.py
```

## Development

```bash
uv sync --group dev
uv run pytest
```

Tests cover config parsing, state aggregation, transcript tail parsing, plugin filename calculation, and the `settings.json` patcher's normalize/upsert primitives. Runtime code is stdlib-only; the dev environment is just for the test runner.

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
