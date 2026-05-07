# agent-status-swiftbar

macOS menu-bar status for active Claude Code and Codex CLI sessions, rendered
through [SwiftBar](https://swiftbar.app).

The plugin listens to agent hook events, writes one small state file per
session, and lets SwiftBar aggregate those sessions into a single menu-bar
symbol. Open the dropdown to see each session, its current state, age, working
directory, latest prompt/summary, and shortcuts back to the originating tab or
folder.

## What It Shows

`agent-status-swiftbar` tracks three live states:

| State | Meaning |
| --- | --- |
| `asking` | The agent is waiting for permission or another user decision. |
| `working` | The agent is processing a prompt or running tools. |
| `waiting` | The session is alive and ready for the next prompt. |

The menu-bar icon is chosen from the highest-priority state across all active
sessions. By default, `asking` wins over `working`, and `working` wins over
`waiting`.

The dropdown includes:

- one row per active Claude Code or Codex CLI session
- current state and elapsed time
- transcript-derived summary when available
- working directory shortcut
- best-effort "Return to Tab" action for Terminal, iTerm, VS Code, Cursor,
  Windsurf, Ghostty, Alacritty, and other detected host apps
- per-session notification toggles

## Requirements

- macOS
- SwiftBar installed at `/Applications/SwiftBar.app`
- Claude Code and/or Codex CLI
- `/usr/bin/python3` with Python 3.10 or newer

Runtime code uses only the Python standard library. Development and asset
generation use optional dependencies from `pyproject.toml`.

Install SwiftBar with Homebrew if needed:

```sh
brew install --cask swiftbar
```

## Install

Clone the repository, then run:

```sh
./install.sh
```

The installer auto-detects Claude Code and Codex CLI. You can force or skip
either integration:

```sh
./install.sh --claude --no-codex
./install.sh --codex --no-claude
```

If SwiftBar has no plugin folder configured, the installer defaults to:

```text
~/Documents/SwiftBar
```

To use another plugin directory for this install:

```sh
SWIFTBAR_PLUGIN_DIR="$HOME/Library/Application Support/SwiftBar/Plugins" ./install.sh
```

## Installed Files

The installer writes these files:

```text
~/.claude/scripts/claudebar.py
~/.claude/scripts/claude-swiftbar-hook.py
~/.claude/scripts/claude-swiftbar-plugin.py
~/.claude/scripts/claude-swiftbar-toggle.py
~/.claude/swiftbar-config.json
~/.claude/state/swiftbar/
<SwiftBar plugin dir>/claude-status.<interval>.sh
```

It also patches hook configuration:

```text
~/.claude/settings.json
~/.codex/hooks.json
```

Existing hook settings are backed up before patching and routes owned by other
tools are left in place.

## Configure

Edit:

```text
~/.claude/swiftbar-config.json
```

Common settings:

```json
{
  "refresh_interval_ms": 500,
  "priority": ["asking", "working", "waiting"],
  "icons": {
    "asking": "exclamationmark.circle",
    "working": "hourglass.circle",
    "waiting": "checkmark.circle",
    "none": "circle.dotted"
  },
  "notifications": {
    "enabled_states": [],
    "sound": false,
    "sound_name": "Glass",
    "include_summary": true
  }
}
```

Most changes are picked up on the next SwiftBar refresh. Changes to
`refresh_interval_ms` also affect the SwiftBar plugin filename, so reinstall or
rename the wrapper when changing the refresh cadence.

Hook routing is configurable with `claude_events` and `codex_events`. Map an
event to a state, or set it to `null` to disable that event:

```json
{
  "claude_events": {
    "UserPromptSubmit": "working",
    "Notification": {
      "permission_prompt": "asking",
      "elicitation_dialog": "asking",
      "idle_prompt": null
    },
    "Stop": "waiting",
    "SessionEnd": "end"
  },
  "codex_events": {
    "PermissionRequest": "asking",
    "Stop": "waiting"
  }
}
```

After changing hook routing in the installed config, regenerate the matching
hook file:

```sh
/usr/bin/python3 scripts/install_settings.py
/usr/bin/python3 scripts/install_codex_hooks.py
```

`./install.sh` also runs these patchers, but it refreshes
`~/.claude/swiftbar-config.json` from the repository template first and leaves
the prior file as `~/.claude/swiftbar-config.json.bak`.

## Notifications

Notifications are off by default. Enable them globally by adding states to
`notifications.enabled_states`, or use the SwiftBar dropdown checkboxes to opt
in per session.

When `terminal-notifier` is available, notifications use it for better macOS
notification behavior and click-back support:

```sh
brew install terminal-notifier
```

Without `terminal-notifier`, the plugin falls back to `osascript`.

## Uninstall

```sh
./uninstall.sh
```

This removes the installed SwiftBar wrapper, deployed scripts, and Claude/Codex
hook routes. It leaves state and config files in place.

To remove those too:

```sh
rm -rf ~/.claude/state/swiftbar
rm -f ~/.claude/swiftbar-config.json
```

## Development

Install development dependencies:

```sh
uv sync --group dev
```

Run tests:

```sh
uv run pytest
```

Useful paths:

```text
lib/claudebar.py                  shared logic
hook/claude-swiftbar-hook.py      hook entry point
plugin/claude-status.py           SwiftBar renderer
plugin/toggle.py                  notification toggle helper
scripts/install_settings.py       Claude hook patcher
scripts/install_codex_hooks.py    Codex hook patcher
```

## License

MIT. See [LICENSE](LICENSE).

## Troubleshooting

If no icon appears:

- confirm SwiftBar is installed in `/Applications`
- confirm SwiftBar has a plugin directory configured
- run `./install.sh` again and then refresh SwiftBar
- open a new Claude Code or Codex CLI session so hooks can write state

If a stale session remains visible, it will be removed automatically when the
agent process is gone or the state file becomes old enough. You can also clear
state manually:

```sh
rm -rf ~/.claude/state/swiftbar
```
