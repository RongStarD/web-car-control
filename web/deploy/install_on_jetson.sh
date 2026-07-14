#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
WEB_ROOT="$PROJECT_ROOT/web"
VENV="$PROJECT_ROOT/.venv"
CONTAINERS=(nifty_dirac sharp_maxwell)

sudo install -d -m 0755 -o "$(id -un)" -g "$(id -gn)" /home/jetson/maps

if [[ ! -f "$WEB_ROOT/frontend/dist/index.html" ]]; then
  printf 'frontend build is missing: %s\n' "$WEB_ROOT/frontend/dist/index.html" >&2
  exit 1
fi

for container in "${CONTAINERS[@]}"; do
  if [[ "$(docker inspect -f '{{.State.Running}}' "$container")" != "true" ]]; then
    continue
  fi
  processes="$(docker top "$container" -eo pid,comm)"
  if printf '%s\n' "$processes" | awk 'NR > 1 && $2 != "bash" { found = 1 } END { exit found ? 0 : 1 }'; then
    printf 'refusing deployment: container %s has active non-shell processes\n' "$container" >&2
    exit 1
  fi
done

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

service_file="$(mktemp)"
trap 'rm -f "$service_file"' EXIT
sed "s|__PROJECT_ROOT__|$PROJECT_ROOT|g" "$WEB_ROOT/deploy/ohcar-web.service" > "$service_file"
sudo install -m 0644 "$service_file" /etc/systemd/system/ohcar-web.service
sudo systemctl daemon-reload
sudo systemctl enable --now ohcar-web.service

printf 'OHCar Web installed. Service: http://0.0.0.0:8080\n'
