#!/usr/bin/env bash
# <bitbar.title>Agent Status</bitbar.title>
# <bitbar.version>0.6</bitbar.version>
# <bitbar.author>local</bitbar.author>
# <bitbar.desc>Aggregate state across active Claude Code and Codex sessions.</bitbar.desc>
# <swiftbar.hideAbout>true</swiftbar.hideAbout>
# <swiftbar.hideRunInTerminal>true</swiftbar.hideRunInTerminal>
# <swiftbar.hideDisablePlugin>true</swiftbar.hideDisablePlugin>
# <swiftbar.hideSwiftBar>true</swiftbar.hideSwiftBar>
# <swiftbar.hideLastUpdated>true</swiftbar.hideLastUpdated>

# Thin wrapper. The real plugin logic lives in claude-swiftbar-plugin.py
# (deployed alongside the hook by install.sh) so the file SwiftBar reads
# is just metadata + an exec.
exec /usr/bin/python3 "$HOME/.claude/scripts/claude-swiftbar-plugin.py"
