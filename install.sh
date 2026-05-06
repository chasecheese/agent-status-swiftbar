#!/usr/bin/env bash
# Install claude-code-swiftbar.
#
# Copies the SwiftBar plugin and the hook script into place, then patches
# ~/.claude/settings.json to register the hooks. Re-running is safe (the
# settings patcher is idempotent and backs up settings.json before writing).
#
# Override the SwiftBar plugin folder by exporting SWIFTBAR_PLUGIN_DIR.

set -euo pipefail

REPO_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

echo "==> Checking SwiftBar"
if [ ! -d "/Applications/SwiftBar.app" ]; then
  cat <<'EOF' >&2
SwiftBar.app not found in /Applications.

Install with Homebrew:
  brew install --cask swiftbar

Or download from https://swiftbar.app
EOF
  exit 1
fi

plugin_dir="${SWIFTBAR_PLUGIN_DIR:-}"
if [ -z "$plugin_dir" ]; then
  plugin_dir="$(defaults read com.ameba.SwiftBar PluginDirectory 2>/dev/null || true)"
fi
if [ -z "$plugin_dir" ]; then
  plugin_dir="$HOME/Documents/SwiftBar"
  echo "    SwiftBar PluginDirectory pref not set; defaulting to $plugin_dir"
fi

mkdir -p "$plugin_dir"
mkdir -p "$HOME/.claude/scripts"
mkdir -p "$HOME/.claude/state/swiftbar"

echo "==> Installing hook -> ~/.claude/scripts/claude-swiftbar-hook.py"
install -m 0755 "$REPO_DIR/hook/claude-swiftbar-hook.py" "$HOME/.claude/scripts/claude-swiftbar-hook.py"

echo "==> Installing plugin -> $plugin_dir/claude-status.2s.sh"
install -m 0755 "$REPO_DIR/plugin/claude-status.2s.sh" "$plugin_dir/claude-status.2s.sh"

echo "==> Wiring hooks into ~/.claude/settings.json"
/usr/bin/python3 "$REPO_DIR/scripts/install_settings.py"

echo "==> Refreshing SwiftBar"
open -g "swiftbar://refreshallplugins" >/dev/null 2>&1 || true

cat <<EOF

Done. Open a Claude Code session — the menu bar icon should turn yellow
(working), then green (done) when Claude finishes, or red when Claude is
waiting on you. Click the icon for per-session details.
EOF
