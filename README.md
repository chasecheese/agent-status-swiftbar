# claude-code-swiftbar

A tiny [SwiftBar](https://swiftbar.app) plugin that turns the macOS menu bar into a status light for [Claude Code](https://claude.com/claude-code) sessions.

The icon shows the most-urgent state across all your active Claude Code windows. Click it for per-session details and one-click "open folder" entries.

## States

Icons are pure SF Symbols — no color — so the menu bar adapts to light/dark automatically. Defaults:

| Default symbol             | State     | Means                                                              |
| -------------------------- | --------- | ------------------------------------------------------------------ |
| `questionmark.circle.fill` | `asking`  | Claude is asking for permission / user input — **you must act**    |
| `bell.circle.fill`         | `notify`  | Passive ping (response ready, idle reminder, auth, ...)            |
| `timer.circle.fill`        | `working` | Claude is running                                                  |
| `circle.badge.checkmark`   | `waiting`    | Claude finished its turn, ready for your next prompt               |
| `circle`                   | `idle`    | Session open, hasn't received any prompt yet                       |
| `circle.dotted`            | `none`    | No active sessions                                                 |

All icons / priority / hook routing are editable in `~/.claude/swiftbar-config.json` — see [Configuration](#configuration).

## Why

If a Claude Code turn takes more than a few seconds you tab away. The menu bar tells you when to come back — without flipping windows.

## Requirements

- macOS
- [SwiftBar](https://swiftbar.app) (`brew install --cask swiftbar`)
- [Claude Code](https://claude.com/claude-code)
- `/usr/bin/python3` (system Python, ships with macOS)

## Install

```bash
git clone https://github.com/<you>/claude-code-swiftbar
cd claude-code-swiftbar
./install.sh
```

The installer is idempotent. It:

1. Copies `hook/claude-swiftbar-hook.py` → `~/.claude/scripts/`.
2. Copies `plugin/claude-status.2s.sh` → your SwiftBar plugin folder (auto-detected; override with `SWIFTBAR_PLUGIN_DIR=/path ./install.sh`).
3. Syncs `plugin/swiftbar-config.json` → `~/.claude/swiftbar-config.json` (backs up the existing file when it differs, as `.bak`).
4. Patches `~/.claude/settings.json` to register hook entries (backs up the previous file).
5. Tells SwiftBar to refresh.

Other tools' hooks on the same events are preserved.

## How it works

Each wired-up Claude Code hook event runs `claude-swiftbar-hook.py <state>`, which writes `~/.claude/state/swiftbar/<session_id>.json`. The SwiftBar plugin polls that directory every two seconds, picks the highest-priority state across sessions, and renders a single SF Symbol.

State files older than 12 hours are ignored (handles the case where Claude Code crashes and skips `SessionEnd`).

## Configuration

Everything tunable lives in `~/.claude/swiftbar-config.json`:

```json
{
  "refresh_interval_ms": 500,
  "icons": {
    "asking":  "questionmark.circle.fill",
    "notify":  "bell.circle.fill",
    "working": "timer.circle.fill",
    "waiting": "circle.badge.checkmark",
    "idle":    "circle",
    "none":    "circle.dotted"
  },
  "priority": ["asking", "notify", "working", "waiting", "idle"],
  "events": {
    "SessionStart":     "idle",
    "UserPromptSubmit": "working",
    "PreToolUse":       "working",
    "PostToolUse":      null,
    "Notification": {
      "permission_prompt":  "asking",
      "elicitation_dialog": "asking",
      "idle_prompt":        "notify",
      "auth_success":       "notify"
    },
    "Stop":             "waiting",
    "SubagentStop":     null,
    "PreCompact":       null,
    "SessionEnd":       "end"
  }
}
```

- **`refresh_interval_ms`** — how often SwiftBar re-runs the plugin, in milliseconds. Floored at 100ms. Encoded into the plugin filename (e.g. `claude-status.0.5s.sh`) by `install.sh`; you have to re-run the installer for changes to this key to take effect.
- **`icons`** — state name → [SF Symbol](https://developer.apple.com/sf-symbols/) name.
- **`priority`** — ordered list used to pick the header icon when multiple sessions are active. Earlier = higher priority. `none` is the implicit fallback.
- **`events`** — every official Claude Code [hook event](https://code.claude.com/docs/en/hooks.md). Each entry can be:
  - a **string** state name → single `matcher=""` route;
  - a **dict** of `matcher → state` → per-subtype routing (e.g. `Notification`, or per-tool `PreToolUse`);
  - `null` / `""` → unwired (any prior wiring of ours is removed on next install).

You can introduce custom state names — just add them to `icons`, slot them into `priority`, and point an event at them.

After editing, run `./install.sh` again. Icons and priority are re-read on every tick (changes apply within one refresh cycle); `events` and `refresh_interval_ms` are baked into `~/.claude/settings.json` and the plugin filename respectively, so they need a re-install.

## Uninstall

```bash
./uninstall.sh
```

Removes the plugin, the hook script, and our entries in `settings.json`. Config (`~/.claude/swiftbar-config.json`) and state files (`~/.claude/state/swiftbar/`) are left in place; the script prints the commands to wipe them if you want.

## Related projects

- [gmr/claude-status](https://github.com/gmr/claude-status) — full menu-bar app with desktop widget; same idea, much more featureful.
- [VibeStatus](https://www.vibestatus.app) — closed-source paid menu bar app with push notifications.
- [PiXeL16/claudecode-macmenu](https://github.com/PiXeL16/claudecode-macmenu) — menu bar app with usage analytics.

This project is the "small SwiftBar plugin you can hack on" version of the same idea.

## License

MIT — see [LICENSE](LICENSE).
