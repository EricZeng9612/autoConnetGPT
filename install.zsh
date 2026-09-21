#!/bin/zsh

set -euo pipefail

readonly SOURCE_DIR="${0:A:h}"
readonly INSTALL_DIR="${HOME}/Library/Application Support/autoConnetGPT"
readonly LOG_DIR="${HOME}/Library/Logs/autoConnetGPT"
readonly AGENT_FILE="${HOME}/Library/LaunchAgents/com.eric.autoConnetGPT.plist"
readonly DOMAIN="gui/$(/usr/bin/id -u)"

/bin/mkdir -p "$INSTALL_DIR" "$LOG_DIR" "${HOME}/Library/LaunchAgents"
/usr/bin/install -m 0755 "$SOURCE_DIR/autoConnetGPT.zsh" "$INSTALL_DIR/autoConnetGPT.zsh"
/usr/bin/install -m 0644 "$SOURCE_DIR/SOP.md" "$INSTALL_DIR/SOP.md"

/usr/bin/sed \
  -e "s|__INSTALL_DIR__|${INSTALL_DIR}|g" \
  -e "s|__LOG_DIR__|${LOG_DIR}|g" \
  "$SOURCE_DIR/com.eric.autoConnetGPT.plist.template" > "${AGENT_FILE}.tmp"
/usr/bin/plutil -lint "${AGENT_FILE}.tmp" >/dev/null
/bin/mv "${AGENT_FILE}.tmp" "$AGENT_FILE"
/bin/chmod 0644 "$AGENT_FILE"

/bin/launchctl bootout "$DOMAIN" "$AGENT_FILE" >/dev/null 2>&1 || true
/bin/launchctl bootstrap "$DOMAIN" "$AGENT_FILE"
/bin/launchctl enable "${DOMAIN}/com.eric.autoConnetGPT"
/bin/launchctl kickstart -k "${DOMAIN}/com.eric.autoConnetGPT"

print -r -- "Installed autoConnetGPT."
print -r -- "Status: $INSTALL_DIR/autoConnetGPT.zsh --status"
print -r -- "Diagnostics: $INSTALL_DIR/autoConnetGPT.zsh --diagnostics"
