#!/usr/bin/env bash
set -euo pipefail

default_map="/root/yahboomcar_ros2_ws/yahboomcar_ws/install/yahboomcar_nav/share/yahboomcar_nav/maps/yahboomcar.yaml"
selection_file="/opt/icar-web/active_map_path"
map_file="$default_map"

if [[ -s "$selection_file" ]]; then
  selected="$(head -n 1 "$selection_file")"
  if [[ -f "$selected" ]]; then
    map_file="$selected"
  fi
fi

exec ros2 run nav2_map_server map_server --ros-args \
  -p use_sim_time:=false \
  -p yaml_filename:="$map_file" \
  -r /tf:=tf \
  -r /tf_static:=tf_static
