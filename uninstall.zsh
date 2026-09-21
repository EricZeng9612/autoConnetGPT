#!/bin/zsh

set -u

readonly INSTALL_DIR="${HOME}/Library/Application Support/autoConnetGPT"
readonly AGENT_FILE="${HOME}/Library/LaunchAgents/com.eric.autoConnetGPT.plist"
readonly DOMAIN="gui/$(/usr/bin/id -u)"

/bin/launchctl bootout "$DOMAIN" "$AGENT_FILE" >/dev/null 2>&1 || true
/bin/rm -f "$AGENT_FILE"
/bin/rm -rf "$INSTALL_DIR"

print -r -- "Uninstalled autoConnetGPT. Logs were preserved in ${HOME}/Library/Logs/autoConnetGPT."
