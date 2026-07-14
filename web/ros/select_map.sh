#!/usr/bin/env bash
set -euo pipefail

selected="${1:-}"
default_map="/root/yahboomcar_ros2_ws/yahboomcar_ws/install/yahboomcar_nav/share/yahboomcar_nav/maps/yahboomcar.yaml"

if [[ "$selected" != "$default_map" && ! "$selected" =~ ^/root/maps/[A-Za-z0-9_-]+\.yaml$ ]]; then
  printf 'invalid map path\n' >&2
  exit 2
fi
if [[ ! -f "$selected" ]]; then
  printf 'map does not exist: %s\n' "$selected" >&2
  exit 3
fi

printf '%s\n' "$selected" > /opt/icar-web/active_map_path.tmp
mv /opt/icar-web/active_map_path.tmp /opt/icar-web/active_map_path
printf 'selected %s\n' "$selected"
