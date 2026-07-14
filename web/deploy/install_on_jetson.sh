#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
WEB_ROOT="$PROJECT_ROOT/web"
VENV="$PROJECT_ROOT/.venv"
CONTAINERS=(nifty_dirac sharp_maxwell)
MODE="full"
ENABLE_CI=false

for argument in "$@"; do
  case "$argument" in
    --runtime-only) MODE="runtime" ;;
    --enable-ci) ENABLE_CI=true ;;
    *) printf 'unknown argument: %s\n' "$argument" >&2; exit 2 ;;
  esac
done

if [[ "$MODE" == "runtime" && "$ENABLE_CI" == "true" ]]; then
  printf '%s\n' '--runtime-only and --enable-ci cannot be combined' >&2
  exit 2
fi

source "$WEB_ROOT/deploy/deploy_guard.sh"
ensure_ohcar_deploy_idle

if [[ ! -f "$WEB_ROOT/frontend/dist/index.html" ]]; then
  printf 'frontend build is missing: %s\n' "$WEB_ROOT/frontend/dist/index.html" >&2
  exit 1
fi

if [[ "$MODE" == "full" ]]; then
  sudo install -d -m 0755 -o "$(id -un)" -g "$(id -gn)" /home/jetson/maps
fi

python3 -m venv "$VENV"
"$VENV/bin/python" -m pip install --upgrade 'pip<26'
"$VENV/bin/python" -m pip install -r "$WEB_ROOT/backend/requirements.txt"

for container in "${CONTAINERS[@]}"; do
  was_running="$(docker inspect -f '{{.State.Running}}' "$container")"
  if [[ "$was_running" != "true" ]]; then
    docker start "$container" >/dev/null
  fi
  docker exec "$container" mkdir -p /opt/icar-web/ros
  docker cp "$WEB_ROOT/ros/." "$container:/opt/icar-web/ros/"
  docker exec "$container" chmod 0755 \
    /opt/icar-web/ros/managed_process.sh \
    /opt/icar-web/ros/velocity_arbiter.py \
    /opt/icar-web/ros/motion_convention.py \
    /opt/icar-web/ros/icar_ros_bridge.py \
    /opt/icar-web/ros/task_guard.py \
    /opt/icar-web/ros/start_map_server.sh \
    /opt/icar-web/ros/select_map.sh
  if [[ "$was_running" != "true" ]]; then
    docker stop "$container" >/dev/null
  fi
done

if [[ "$MODE" == "runtime" ]]; then
  if [[ ! -x /usr/local/sbin/ohcar-web-restart ]]; then
    printf '%s\n' 'CI restart helper is missing; run install_on_jetson.sh --enable-ci once' >&2
    exit 1
  fi
  sudo -n /usr/local/sbin/ohcar-web-restart
else
  temporary_directory="$(mktemp -d)"
  trap 'rm -rf "$temporary_directory"' EXIT
  service_file="$temporary_directory/ohcar-web.service"
  sed "s|__PROJECT_ROOT__|$PROJECT_ROOT|g" "$WEB_ROOT/deploy/ohcar-web.service" > "$service_file"
  sudo install -m 0644 "$service_file" /etc/systemd/system/ohcar-web.service

  if [[ "$ENABLE_CI" == "true" ]]; then
    sudo install -m 0755 -o root -g root \
      "$WEB_ROOT/deploy/restart_ohcar_web.sh" \
      /usr/local/sbin/ohcar-web-restart
    sudoers_file="$temporary_directory/ohcar-cd"
    printf '%s ALL=(root) NOPASSWD: /usr/local/sbin/ohcar-web-restart\n' "$(id -un)" > "$sudoers_file"
    sudo visudo -cf "$sudoers_file"
    sudo install -m 0440 -o root -g root "$sudoers_file" /etc/sudoers.d/ohcar-cd
  fi

  sudo systemctl daemon-reload
  sudo systemctl enable --now ohcar-web.service
fi

printf 'OHCar Web installed. Service: http://0.0.0.0:8080\n'
