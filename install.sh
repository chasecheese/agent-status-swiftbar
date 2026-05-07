#!/usr/bin/env bash
# Install agent-status-swiftbar.
#
# Deploys (in order):
#   ~/.claude/scripts/claudebar.py                  shared lib
#   ~/.claude/scripts/claude-swiftbar-hook.py       hook entry (Claude + Codex)
#   ~/.claude/scripts/claude-swiftbar-plugin.py     plugin entry
#   ~/.claude/scripts/claude-swiftbar-toggle.py     dropdown click callback
#   ~/.claude/swiftbar-config.json                  user config (preserves edits via .bak)
#   <SwiftBar plugin dir>/claude-status.<interval>.sh   bash wrapper SwiftBar runs
#   ~/.claude/settings.json                         (patched: Claude hook routes)  [Claude]
#   ~/.codex/hooks.json                             (patched: Codex hook routes)   [Codex]
#
# Flags (defaults: auto-detect each):
#   --claude / --no-claude    force install / skip Claude integration
#   --codex  / --no-codex     force install / skip Codex  integration
#
# Idempotent. Override SwiftBar plugin folder with SWIFTBAR_PLUGIN_DIR.

set -euo pipefail

REPO_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PY=/usr/bin/python3
LIB="$REPO_DIR/lib"

INSTALL_CLAUDE=auto
INSTALL_CODEX=auto

while [ $# -gt 0 ]; do
  case "$1" in
    --claude)    INSTALL_CLAUDE=yes ;;
    --no-claude) INSTALL_CLAUDE=no  ;;
    --codex)     INSTALL_CODEX=yes  ;;
    --no-codex)  INSTALL_CODEX=no   ;;
    -h|--help)
      sed -n '/^# /,/^$/p' "${BASH_SOURCE[0]}" | sed 's/^# \?//'
      exit 0
      ;;
    *)
      echo "unknown flag: $1" >&2
      exit 2
      ;;
  esac
  shift
done

resolve_auto() {
  # $1 = current value, $2 = home dir to look for, $3 = CLI binary name
  if [ "$1" != auto ]; then echo "$1"; return; fi
  if [ -d "$HOME/$2" ] || command -v "$3" >/dev/null 2>&1; then
    echo yes
  else
    echo no
  fi
}
INSTALL_CLAUDE=$(resolve_auto "$INSTALL_CLAUDE" .claude claude)
INSTALL_CODEX=$(resolve_auto "$INSTALL_CODEX" .codex codex)

if [ "$INSTALL_CLAUDE" = no ] && [ "$INSTALL_CODEX" = no ]; then
  echo "Neither Claude Code nor Codex CLI was detected; pass --claude or --codex to force." >&2
  exit 2
fi

echo "==> Targets: claude=$INSTALL_CLAUDE  codex=$INSTALL_CODEX"

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

claude_scripts="$HOME/.claude/scripts"
mkdir -p "$plugin_dir" "$claude_scripts" "$HOME/.claude/state/swiftbar"

echo "==> Installing shared lib -> $claude_scripts/claudebar.py"
install -m 0644 "$LIB/claudebar.py" "$claude_scripts/claudebar.py"

echo "==> Installing hook -> $claude_scripts/claude-swiftbar-hook.py"
install -m 0755 "$REPO_DIR/hook/claude-swiftbar-hook.py" "$claude_scripts/claude-swiftbar-hook.py"

echo "==> Installing plugin entry -> $claude_scripts/claude-swiftbar-plugin.py"
install -m 0755 "$REPO_DIR/plugin/claude-status.py" "$claude_scripts/claude-swiftbar-plugin.py"

echo "==> Installing toggle helper -> $claude_scripts/claude-swiftbar-toggle.py"
install -m 0755 "$REPO_DIR/plugin/toggle.py" "$claude_scripts/claude-swiftbar-toggle.py"

config_src="$REPO_DIR/plugin/swiftbar-config.json"
config_dst="$HOME/.claude/swiftbar-config.json"
if [ -f "$config_dst" ] && ! cmp -s "$config_src" "$config_dst"; then
  cp "$config_dst" "$config_dst.bak"
  echo "==> Updating config -> $config_dst (backup at $config_dst.bak)"
elif [ ! -f "$config_dst" ]; then
  echo "==> Installing config -> $config_dst"
fi
install -m 0644 "$config_src" "$config_dst"

# Plugin filename encodes refresh interval — SwiftBar parses it from the name.
plugin_name=$("$PY" -c "
import sys; sys.path.insert(0, '$LIB')
from claudebar import load_config, plugin_filename_for
print(plugin_filename_for(load_config()['refresh_interval_ms']))
")
plugin_dst="$plugin_dir/$plugin_name"

# Strip any prior install (refresh interval may have changed → different filename).
find "$plugin_dir" -maxdepth 1 -name 'claude-status.*.sh' ! -name "$plugin_name" -delete 2>/dev/null || true

echo "==> Installing plugin wrapper -> $plugin_dst"
install -m 0755 "$REPO_DIR/plugin/claude-status.sh" "$plugin_dst"

if [ "$INSTALL_CLAUDE" = yes ]; then
  echo "==> Wiring Claude hooks into ~/.claude/settings.json"
  "$PY" "$REPO_DIR/scripts/install_settings.py"
fi

if [ "$INSTALL_CODEX" = yes ]; then
  echo "==> Wiring Codex hooks into ~/.codex/hooks.json"
  "$PY" "$REPO_DIR/scripts/install_codex_hooks.py"
fi

echo "==> Refreshing SwiftBar"
open -g "swiftbar://refreshallplugins" >/dev/null 2>&1 || true

cat <<EOF

Done. Open a Claude Code or Codex session — the menu bar SF Symbol
changes as the agent starts, works, finishes, or waits on you. Click
the icon for per-session details. Customize symbols, priority, hook
routing, and refresh interval in:
  ~/.claude/swiftbar-config.json
EOF
