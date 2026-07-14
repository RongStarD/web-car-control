#!/usr/bin/env bash
set -u

STATE_DIR="${ICAR_MANAGER_STATE_DIR:-/tmp/icar-system-manager}"
ACTION="${1:-}"
NAME="${2:-}"

mkdir -p "$STATE_DIR"

valid_name() {
  [[ "$1" =~ ^[a-zA-Z0-9_-]+$ ]]
}

pid_file() { printf '%s/%s.pid' "$STATE_DIR" "$1"; }
log_file() { printf '%s/%s.log' "$STATE_DIR" "$1"; }
exit_file() { printf '%s/%s.exit' "$STATE_DIR" "$1"; }
command_file() { printf '%s/%s.command' "$STATE_DIR" "$1"; }
started_file() { printf '%s/%s.started' "$STATE_DIR" "$1"; }

read_live_pid() {
  local file pid
  file="$(pid_file "$1")"
  [[ -f "$file" ]] || return 1
  pid="$(cat "$file")"
  [[ "$pid" =~ ^[0-9]+$ ]] || return 1
  kill -0 "$pid" 2>/dev/null || return 1
  printf '%s' "$pid"
}

stop_group() {
  local name="$1" pid=""
  pid="$(read_live_pid "$name")" || {
    rm -f "$(pid_file "$name")"
    printf 'stopped\n'
    return 0
  }

  kill -INT -- "-$pid" 2>/dev/null || kill -INT "$pid" 2>/dev/null || true
  for _ in $(seq 1 30); do
    kill -0 "$pid" 2>/dev/null || break
    sleep 0.1
  done
  if kill -0 "$pid" 2>/dev/null; then
    kill -TERM -- "-$pid" 2>/dev/null || kill -TERM "$pid" 2>/dev/null || true
  fi
  for _ in $(seq 1 20); do
    kill -0 "$pid" 2>/dev/null || break
    sleep 0.1
  done
  if kill -0 "$pid" 2>/dev/null; then
    kill -KILL -- "-$pid" 2>/dev/null || kill -KILL "$pid" 2>/dev/null || true
  fi
  rm -f "$(pid_file "$name")"
  printf 'stopped\n'
}

case "$ACTION" in
  start)
    valid_name "$NAME" || { printf 'invalid process name\n' >&2; exit 2; }
    COMMAND="${3:-}"
    [[ -n "$COMMAND" ]] || { printf 'missing configured command\n' >&2; exit 2; }
    if PID="$(read_live_pid "$NAME")"; then
      printf 'running %s\n' "$PID"
      exit 0
    fi
    rm -f "$(pid_file "$NAME")" "$(exit_file "$NAME")"
    printf '%s\n' "$COMMAND" > "$(command_file "$NAME")"
    date -Iseconds > "$(started_file "$NAME")"
    printf '\n[%s] starting: %s\n' "$(date -Iseconds)" "$COMMAND" >> "$(log_file "$NAME")"
    export ICAR_MANAGED_COMMAND="$COMMAND"
    export ICAR_MANAGED_EXIT_FILE="$(exit_file "$NAME")"
    nohup setsid bash -lic 'set +e; eval "$ICAR_MANAGED_COMMAND"; code=$?; printf "%s\n" "$code" > "$ICAR_MANAGED_EXIT_FILE"; exit "$code"' >> "$(log_file "$NAME")" 2>&1 < /dev/null &
    PID=$!
    printf '%s\n' "$PID" > "$(pid_file "$NAME")"
    sleep 0.5
    if kill -0 "$PID" 2>/dev/null; then
      printf 'started %s\n' "$PID"
    else
      rm -f "$(pid_file "$NAME")"
      printf 'configured command exited during startup\n' >&2
      tail -n 20 "$(log_file "$NAME")" >&2 || true
      exit 1
    fi
    ;;
  stop)
    valid_name "$NAME" || { printf 'invalid process name\n' >&2; exit 2; }
    stop_group "$NAME"
    ;;
  status)
    valid_name "$NAME" || { printf 'invalid process name\n' >&2; exit 2; }
    if PID="$(read_live_pid "$NAME")"; then
      printf 'running %s\n' "$PID"
    else
      rm -f "$(pid_file "$NAME")"
      if [[ -f "$(exit_file "$NAME")" ]]; then
        printf 'exited %s\n' "$(cat "$(exit_file "$NAME")")"
      else
        printf 'stopped\n'
      fi
    fi
    ;;
  run-once)
    valid_name "$NAME" || { printf 'invalid process name\n' >&2; exit 2; }
    TIMEOUT="${3:-45}"
    COMMAND="${4:-}"
    [[ "$TIMEOUT" =~ ^[0-9]+$ ]] || { printf 'invalid timeout\n' >&2; exit 2; }
    [[ -n "$COMMAND" ]] || { printf 'missing configured command\n' >&2; exit 2; }
    printf '\n[%s] run once: %s\n' "$(date -Iseconds)" "$COMMAND" >> "$(log_file "$NAME")"
    timeout --signal=INT --kill-after=3 "$TIMEOUT" bash -lic "$COMMAND" 2>&1 | tee -a "$(log_file "$NAME")"
    ;;
  tail)
    valid_name "$NAME" || { printf 'invalid process name\n' >&2; exit 2; }
    LINES="${3:-80}"
    [[ "$LINES" =~ ^[0-9]+$ ]] || LINES=80
    tail -n "$LINES" "$(log_file "$NAME")" 2>/dev/null || true
    ;;
  nodes)
    bash -lic 'ros2 node list' 2>/dev/null | sed -n '/^\//p'
    ;;
  lifecycle)
    NODE="${3:-}"
    [[ "$NODE" =~ ^/?[a-zA-Z0-9_/-]+$ ]] || { printf 'invalid node name\n' >&2; exit 2; }
    bash -lic "ros2 lifecycle get /${NODE#/}" 2>/dev/null
    ;;
  lifecycle-set)
    NODE="${3:-}"
    TRANSITION="${4:-}"
    [[ "$NODE" =~ ^/?[a-zA-Z0-9_/-]+$ ]] || { printf 'invalid node name\n' >&2; exit 2; }
    [[ "$TRANSITION" =~ ^(configure|activate|deactivate|cleanup|shutdown)$ ]] || { printf 'invalid lifecycle transition\n' >&2; exit 2; }
    bash -lic "ros2 lifecycle set /${NODE#/} $TRANSITION"
    ;;
  *)
    printf 'usage: %s {start|stop|status|run-once|tail|nodes|lifecycle|lifecycle-set} name [...]\n' "$0" >&2
    exit 2
    ;;
esac
