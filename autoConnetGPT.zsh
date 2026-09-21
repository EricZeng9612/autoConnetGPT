#!/bin/zsh

# autoConnetGPT - keep a Mac reachable for ChatGPT/Codex Remote and repair
# common local connectivity failures without restarting healthy tasks.

set -u
setopt NO_NOMATCH

export PATH="/usr/bin:/bin:/usr/sbin:/sbin"

readonly PROGRAM_NAME="autoConnetGPT"
readonly VERSION="1.0.0"
readonly CHATGPT_APP="/Applications/ChatGPT.app"
readonly CLASH_APP="/Applications/Clash Verge.app"
readonly PROXY_URL="${AUTOCONNETGPT_PROXY_URL:-http://127.0.0.1:7897}"
readonly PROXY_PORT="${AUTOCONNETGPT_PROXY_PORT:-7897}"
readonly CHECK_URL="${AUTOCONNETGPT_CHECK_URL:-https://chatgpt.com/}"
readonly CHECK_INTERVAL="${AUTOCONNETGPT_CHECK_INTERVAL:-3600}"
readonly STATE_DIR="${HOME}/Library/Application Support/autoConnetGPT"
readonly LOG_DIR="${HOME}/Library/Logs/autoConnetGPT"
readonly LOG_FILE="${LOG_DIR}/autoConnetGPT.log"
readonly STATUS_FILE="${STATE_DIR}/status.txt"
readonly LOCK_DIR="${STATE_DIR}/check.lock"
readonly MAX_LOG_BYTES=5242880

mkdir -p "$STATE_DIR" "$LOG_DIR"

timestamp() {
  /bin/date '+%Y-%m-%d %H:%M:%S %z'
}

rotate_log() {
  local size=0
  if [[ -f "$LOG_FILE" ]]; then
    size=$(/usr/bin/stat -f '%z' "$LOG_FILE" 2>/dev/null || print 0)
  fi
  if (( size > MAX_LOG_BYTES )); then
    [[ -f "${LOG_FILE}.1" ]] && /bin/rm -f "${LOG_FILE}.1"
    /bin/mv "$LOG_FILE" "${LOG_FILE}.1"
  fi
}

log() {
  rotate_log
  print -r -- "[$(timestamp)] $*" >> "$LOG_FILE"
}

notify_user() {
  local title="$1"
  local message="$2"
  /usr/bin/osascript -e "display notification \"${message}\" with title \"${title}\"" >/dev/null 2>&1 || true
}

is_chatgpt_running() {
  /bin/ps -axo args= | /usr/bin/awk '
    index($0, "/Applications/ChatGPT.app/Contents/MacOS/ChatGPT") == 1 { found=1 }
    END { exit !found }
  '
}

is_app_server_running() {
  /bin/ps -axo args= | /usr/bin/awk '
    index($0, "/Applications/ChatGPT.app/Contents/Resources/codex ") == 1 && $0 ~ / app-server([[:space:]]|$)/ { found=1 }
    END { exit !found }
  '
}

is_clash_running() {
  /bin/ps -axo args= | /usr/bin/awk '
    index($0, "/Applications/Clash Verge.app/Contents/MacOS/clash-verge") == 1 ||
    index($0, "/Applications/Clash Verge.app/Contents/MacOS/verge-mihomo") == 1 { found=1 }
    END { exit !found }
  '
}

is_proxy_listening() {
  /usr/sbin/lsof -nP -iTCP:"${PROXY_PORT}" -sTCP:LISTEN 2>/dev/null | /usr/bin/grep -q LISTEN
}

probe_openai() {
  local result code
  result=$(/usr/bin/curl \
    --proxy "$PROXY_URL" \
    --connect-timeout 8 \
    --max-time 20 \
    --location \
    --silent \
    --show-error \
    --output /dev/null \
    --write-out '%{http_code}' \
    "$CHECK_URL" 2>>"$LOG_FILE") || return 1
  code="${result[-3,-1]}"
  [[ "$code" == <200-499> ]] || return 1
  print -r -- "$code"
}

wait_for_proxy() {
  local attempts="${1:-8}"
  local delay="${2:-3}"
  local i code
  for (( i = 1; i <= attempts; i++ )); do
    if is_proxy_listening; then
      code=$(probe_openai 2>/dev/null) && {
        print -r -- "$code"
        return 0
      }
    fi
    /bin/sleep "$delay"
  done
  return 1
}

start_clash() {
  [[ -d "$CLASH_APP" ]] || return 1
  log "ACTION launching Clash Verge"
  /usr/bin/open -gja "$CLASH_APP" >/dev/null 2>&1 || return 1
  wait_for_proxy 7 3 >/dev/null
}

restart_clash() {
  [[ -d "$CLASH_APP" ]] || return 1
  log "ACTION restarting Clash Verge after failed launch recovery"
  /usr/bin/osascript -e 'tell application "Clash Verge" to quit' >/dev/null 2>&1 || true
  /bin/sleep 5
  /usr/bin/open -gja "$CLASH_APP" >/dev/null 2>&1 || return 1
  wait_for_proxy 8 3 >/dev/null
}

wait_for_chatgpt() {
  local attempts="${1:-20}"
  local i
  for (( i = 1; i <= attempts; i++ )); do
    if is_chatgpt_running && is_app_server_running; then
      return 0
    fi
    /bin/sleep 3
  done
  return 1
}

start_chatgpt() {
  [[ -d "$CHATGPT_APP" ]] || return 1
  log "ACTION launching ChatGPT"
  /usr/bin/open -gja "$CHATGPT_APP" >/dev/null 2>&1 || return 1
  wait_for_chatgpt 20
}

restart_chatgpt() {
  [[ -d "$CHATGPT_APP" ]] || return 1
  log "ACTION restarting ChatGPT because App Server is missing while VPN is healthy"
  /usr/bin/osascript -e 'tell application "ChatGPT" to quit' >/dev/null 2>&1 || true
  /bin/sleep 5
  /usr/bin/open -gja "$CHATGPT_APP" >/dev/null 2>&1 || return 1
  wait_for_chatgpt 20
}

configured_sleep_minutes() {
  /usr/bin/pmset -g custom 2>/dev/null | /usr/bin/awk '$1 == "sleep" { print $2; exit }'
}

previous_result() {
  [[ -f "$STATUS_FILE" ]] || return 0
  /usr/bin/awk -F= '$1 == "result" { print substr($0, index($0, "=") + 1); exit }' "$STATUS_FILE"
}

daemon_is_running() {
  /bin/launchctl print "gui/$(/usr/bin/id -u)/com.eric.autoConnetGPT" 2>/dev/null |
    /usr/bin/grep -q 'state = running'
}

write_status() {
  local result="$1"
  local detail="$2"
  local http_code="$3"
  local clash_state="$4"
  local proxy_state="$5"
  local chatgpt_state="$6"
  local app_server_state="$7"
  local sleep_minutes="$8"
  local sleep_protected="$9"
  local tmp_file="${STATUS_FILE}.tmp.$$"

  {
    print -r -- "program=${PROGRAM_NAME}"
    print -r -- "version=${VERSION}"
    print -r -- "checked_at=$(timestamp)"
    print -r -- "result=${result}"
    print -r -- "detail=${detail}"
    print -r -- "openai_http=${http_code}"
    print -r -- "clash=${clash_state}"
    print -r -- "proxy=${proxy_state}"
    print -r -- "chatgpt=${chatgpt_state}"
    print -r -- "app_server=${app_server_state}"
    print -r -- "configured_sleep_minutes=${sleep_minutes:-unknown}"
    print -r -- "guard_prevents_idle_sleep=${sleep_protected}"
    print -r -- "next_check_seconds=${CHECK_INTERVAL}"
    print -r -- "log=${LOG_FILE}"
  } > "$tmp_file"
  /bin/mv "$tmp_file" "$STATUS_FILE"
}

run_check() {
  local old_result result detail http_code
  local clash_state proxy_state chatgpt_state app_server_state
  local sleep_minutes sleep_protected vpn_repaired=false codex_repaired=false

  if ! /bin/mkdir "$LOCK_DIR" 2>/dev/null; then
    local lock_pid=""
    [[ -f "${LOCK_DIR}/pid" ]] && lock_pid=$(/bin/cat "${LOCK_DIR}/pid" 2>/dev/null)
    if [[ -n "$lock_pid" ]] && /bin/kill -0 "$lock_pid" 2>/dev/null; then
      log "SKIP another health check is already running pid=${lock_pid}"
      return 0
    fi
    log "WARN removing stale health-check lock"
    /bin/rm -rf "$LOCK_DIR"
    /bin/mkdir "$LOCK_DIR" 2>/dev/null || {
      log "ERROR unable to acquire health-check lock"
      return 1
    }
  fi
  print -r -- "$$" > "${LOCK_DIR}/pid"

  old_result=$(previous_result)
  sleep_minutes=$(configured_sleep_minutes)
  sleep_protected="${AUTOCONNETGPT_SLEEP_PROTECTED:-no}"
  if [[ "$sleep_protected" != "yes" ]] && daemon_is_running; then
    sleep_protected="yes"
  fi
  result="HEALTHY"
  detail="All monitored components are healthy"
  http_code="unknown"

  log "CHECK begin"

  if ! is_proxy_listening || ! http_code=$(probe_openai); then
    log "WARN VPN proxy or OpenAI route is unhealthy"
    if start_clash; then
      vpn_repaired=true
      log "RECOVERY Clash launch restored connectivity"
    elif restart_clash; then
      vpn_repaired=true
      log "RECOVERY Clash restart restored connectivity"
    else
      log "ERROR unable to restore VPN connectivity"
    fi
  fi

  http_code=$(probe_openai 2>/dev/null) || http_code="unreachable"

  if [[ "$http_code" == "unreachable" ]]; then
    result="VPN_NEEDS_ATTENTION"
    detail="Clash or its selected VPN node cannot reach ChatGPT"
  else
    if ! is_chatgpt_running; then
      if start_chatgpt; then
        codex_repaired=true
        log "RECOVERY ChatGPT was launched"
      else
        result="REMOTE_NEEDS_ATTENTION"
        detail="ChatGPT could not be launched"
      fi
    elif ! is_app_server_running; then
      if restart_chatgpt; then
        codex_repaired=true
        log "RECOVERY ChatGPT restart restored Codex App Server"
      else
        result="REMOTE_NEEDS_ATTENTION"
        detail="Codex App Server is missing after ChatGPT restart"
      fi
    fi
  fi

  clash_state="stopped"; is_clash_running && clash_state="running"
  proxy_state="not_listening"; is_proxy_listening && proxy_state="listening"
  chatgpt_state="stopped"; is_chatgpt_running && chatgpt_state="running"
  app_server_state="stopped"; is_app_server_running && app_server_state="running"

  if [[ "$result" == "HEALTHY" ]]; then
    if [[ "$codex_repaired" == true ]]; then
      result="CODEX_REPAIRED"
      detail="ChatGPT/Codex was automatically restored"
    elif [[ "$vpn_repaired" == true ]]; then
      result="VPN_REPAIRED"
      detail="Clash/VPN connectivity was automatically restored"
    elif [[ -n "$sleep_minutes" && "$sleep_minutes" != "0" && "$sleep_protected" != "yes" ]]; then
      result="HOST_SLEEP_RISK"
      detail="macOS still permits idle system sleep and the guard is not holding an assertion"
    fi
  fi

  write_status "$result" "$detail" "$http_code" "$clash_state" "$proxy_state" \
    "$chatgpt_state" "$app_server_state" "$sleep_minutes" "$sleep_protected"
  log "CHECK result=${result} http=${http_code} clash=${clash_state} proxy=${proxy_state} chatgpt=${chatgpt_state} app_server=${app_server_state}"

  if [[ "$result" == *_NEEDS_ATTENTION || "$result" == "HOST_SLEEP_RISK" ]]; then
    if [[ "$old_result" != "$result" ]]; then
      notify_user "$PROGRAM_NAME 需要处理" "$detail"
    fi
  elif [[ -n "$old_result" && "$old_result" == *_NEEDS_ATTENTION ]]; then
    notify_user "$PROGRAM_NAME 已恢复" "$detail"
  fi

  /bin/rm -rf "$LOCK_DIR" 2>/dev/null || true
  return 0
}

show_status() {
  if [[ -f "$STATUS_FILE" ]]; then
    /bin/cat "$STATUS_FILE"
  else
    print -r -- "No status is available yet. Run: $0 --check"
    return 1
  fi
}

show_diagnostics() {
  print -r -- "== autoConnetGPT ${VERSION} =="
  show_status 2>/dev/null || true
  print -r -- "\n== Power assertions =="
  /usr/bin/pmset -g assertions 2>/dev/null | /usr/bin/head -n 35
  print -r -- "\n== Relevant processes =="
  /bin/ps -axo pid=,args= | /usr/bin/grep -E 'ChatGPT.app|clash-verge|verge-mihomo|autoConnetGPT|caffeinate' | /usr/bin/grep -v grep
  print -r -- "\n== Recent log =="
  /usr/bin/tail -n 30 "$LOG_FILE" 2>/dev/null || true
}

daemon_main() {
  local caffeinate_pid
  export AUTOCONNETGPT_SLEEP_PROTECTED=yes
  log "START daemon version=${VERSION} interval=${CHECK_INTERVAL}s"

  /usr/bin/caffeinate -is -w $$ >/dev/null 2>&1 &
  caffeinate_pid=$!
  trap '/bin/kill '"$caffeinate_pid"' >/dev/null 2>&1 || true; /bin/rm -rf '"${LOCK_DIR:q}"' >/dev/null 2>&1 || true' EXIT TERM INT HUP

  while true; do
    run_check
    /bin/sleep "$CHECK_INTERVAL"
  done
}

case "${1:---daemon}" in
  --daemon) daemon_main ;;
  --check) run_check; show_status ;;
  --status) show_status ;;
  --diagnostics) show_diagnostics ;;
  --version) print -r -- "${PROGRAM_NAME} ${VERSION}" ;;
  *)
    print -r -- "Usage: $0 [--daemon|--check|--status|--diagnostics|--version]"
    exit 2
    ;;
esac
