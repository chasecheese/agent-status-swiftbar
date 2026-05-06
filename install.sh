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

config_src="$REPO_DIR/plugin/swiftbar-config.json"
config_dst="$HOME/.claude/swiftbar-config.json"
if [ -f "$config_dst" ] && ! cmp -s "$config_src" "$config_dst"; then
  cp "$config_dst" "$config_dst.bak"
  echo "==> Updating config -> $config_dst (backup at $config_dst.bak)"
elif [ ! -f "$config_dst" ]; then
  echo "==> Installing config -> $config_dst"
fi
install -m 0644 "$config_src" "$config_dst"

# Plugin filename encodes the refresh interval (SwiftBar reads it from the name).
# Use 'ms' for sub-second / non-multiple-of-1000 intervals, 's' otherwise.
# (SwiftBar reliably parses both; '0.5s' is not honored uniformly.)
plugin_name=$(/usr/bin/python3 - <<PY
import json
try:
    cfg = json.load(open("$config_src"))
    ms = max(100, int(cfg.get("refresh_interval_ms", 1000)))
except Exception:
    ms = 1000
suffix = f"{ms // 1000}s" if ms % 1000 == 0 else f"{ms}ms"
print(f"claude-status.{suffix}.sh")
PY
)
plugin_dst="$plugin_dir/$plugin_name"

# Strip any prior install (the interval suffix may have changed).
find "$plugin_dir" -maxdepth 1 -name 'claude-status.*.sh' ! -name "$plugin_name" -delete 2>/dev/null || true

echo "==> Installing plugin -> $plugin_dst"
install -m 0755 "$REPO_DIR/plugin/claude-status.2s.sh" "$plugin_dst"

echo "==> Wiring hooks into ~/.claude/settings.json"
/usr/bin/python3 "$REPO_DIR/scripts/install_settings.py"

echo "==> Refreshing SwiftBar"
open -g "swiftbar://refreshallplugins" >/dev/null 2>&1 || true

cat <<EOF

Done. Open a Claude Code session — the menu bar SF Symbol changes as
Claude starts, works, finishes, or waits on you. Click the icon for
per-session details. Customize symbols, priority, hook routing, and
refresh interval in:
  ~/.claude/swiftbar-config.json
EOF
