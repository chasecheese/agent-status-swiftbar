# claude-code-swiftbar

A tiny [SwiftBar](https://swiftbar.app) plugin that turns the macOS menu bar into a status light for [Claude Code](https://claude.com/claude-code) sessions.

- 🔴 **Waiting** — Claude is asking for your input or permission
- 🟡 **Working** — Claude is doing something
- 🟢 **Done** — Claude finished its turn
- ⚪ **Idle** — session open, no active turn
- ⚫ **Off** — no active sessions

Multiple Claude Code windows? The icon shows the count and uses the most-urgent state across all of them. Click the icon to see one line per session (state, folder, age) with a one-click "Open folder" entry.

## Why?

If you let Claude Code run for a few minutes per turn, you probably tab away. The menu bar icon tells you when to come back — without flipping through windows.

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

The installer:
1. Copies `hook/claude-swiftbar-hook.py` to `~/.claude/scripts/`.
2. Copies `plugin/claude-status.2s.sh` to your SwiftBar plugin folder (auto-detected, override with `SWIFTBAR_PLUGIN_DIR=/path ./install.sh`).
3. Patches `~/.claude/settings.json` to register five hooks (a backup is written to `settings.json.bak`).
4. Asks SwiftBar to reload.

It's idempotent and preserves any other hooks you already have on the same events.

## How it works

| Hook event         | State written |
| ------------------ | ------------- |
| `SessionStart`     | `idle`        |
| `UserPromptSubmit` | `working`     |
| `Stop`             | `done`        |
| `Notification`     | `waiting`     |
| `SessionEnd`       | (file deleted) |

Each hook fires `claude-swiftbar-hook.py <state>`, which writes `~/.claude/state/swiftbar/<session_id>.json`. The SwiftBar plugin polls that directory every two seconds, picks the highest-priority state, and renders.

State files older than 12 hours are ignored (handles the case where a Claude Code crash skips `SessionEnd`).

## Customizing

- **Refresh interval** — rename `claude-status.2s.sh` to `claude-status.5s.sh` (or `1s`, `1m`, etc.). SwiftBar reads the cadence from the filename.
- **Icons / colors** — edit the `ICONS` and `COLORS` dicts at the top of `plugin/claude-status.2s.sh`. The icons are SF Symbol names; any symbol from [SF Symbols.app](https://developer.apple.com/sf-symbols/) works.
- **State priority** — change the `PRIORITY` list in the same file.

After editing, run `open -g "swiftbar://refreshallplugins"` (or click the SwiftBar icon → Refresh All) to reload.

## Uninstall

```bash
./uninstall.sh
```

Removes the plugin, the hook script, and the hook entries in `settings.json`. State files in `~/.claude/state/swiftbar/` are left alone — `rm -rf ~/.claude/state/swiftbar` to clean those up too.

## Related projects

- [gmr/claude-status](https://github.com/gmr/claude-status) — full menu-bar app with desktop widget; same idea, much more featureful.
- [VibeStatus](https://www.vibestatus.app) — closed-source paid menu bar app with push notifications.
- [PiXeL16/claudecode-macmenu](https://github.com/PiXeL16/claudecode-macmenu) — menu bar app with usage analytics.

This project is the "thirty lines of Python in a SwiftBar plugin" version of the same idea, for people who already use SwiftBar and want something they can hack on.

## License

MIT — see [LICENSE](LICENSE).
