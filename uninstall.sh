#!/usr/bin/env bash
# Remove agent-status-swiftbar.

set -euo pipefail

REPO_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

plugin_dir="${SWIFTBAR_PLUGIN_DIR:-}"
if [ -z "$plugin_dir" ]; then
  plugin_dir="$(defaults read com.ameba.SwiftBar PluginDirectory 2>/dev/null || true)"
fi
plugin_dir="${plugin_dir:-$HOME/Documents/SwiftBar}"

echo "==> Removing Claude hooks from ~/.claude/settings.json"
/usr/bin/python3 "$REPO_DIR/scripts/uninstall_settings.py"

echo "==> Removing Codex hooks from ~/.codex/hooks.json"
/usr/bin/python3 "$REPO_DIR/scripts/uninstall_codex_hooks.py"

echo "==> Removing plugin from $plugin_dir"
rm -f "$plugin_dir"/claude-status.*.sh

echo "==> Removing scripts under ~/.claude/scripts"
rm -f "$HOME/.claude/scripts/claude-swiftbar-hook.py"
rm -f "$HOME/.claude/scripts/claude-swiftbar-plugin.py"
rm -f "$HOME/.claude/scripts/claude-swiftbar-toggle.py"
rm -f "$HOME/.claude/scripts/claudebar.py"

echo "==> Refreshing SwiftBar"
open -g "swiftbar://refreshallplugins" >/dev/null 2>&1 || true

cat <<EOF

Done. State files and config left in place. To wipe them too:
  rm -rf ~/.claude/state/swiftbar
  rm -f  ~/.claude/swiftbar-config.json
EOF
