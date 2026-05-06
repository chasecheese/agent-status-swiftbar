#!/usr/bin/env bash
# Remove claude-code-swiftbar.

set -euo pipefail

REPO_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

plugin_dir="${SWIFTBAR_PLUGIN_DIR:-}"
if [ -z "$plugin_dir" ]; then
  plugin_dir="$(defaults read com.ameba.SwiftBar PluginDirectory 2>/dev/null || true)"
fi
plugin_dir="${plugin_dir:-$HOME/Documents/SwiftBar}"

echo "==> Removing hooks from ~/.claude/settings.json"
/usr/bin/python3 "$REPO_DIR/scripts/uninstall_settings.py"

echo "==> Removing plugin from $plugin_dir"
rm -f "$plugin_dir/claude-status.2s.sh"

echo "==> Removing hook script"
rm -f "$HOME/.claude/scripts/claude-swiftbar-hook.py"

echo "==> Refreshing SwiftBar"
open -g "swiftbar://refreshallplugins" >/dev/null 2>&1 || true

cat <<EOF

Done. State files in ~/.claude/state/swiftbar/ were left in place.
Remove them with:
  rm -rf ~/.claude/state/swiftbar
EOF
