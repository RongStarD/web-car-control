#!/usr/bin/env bash
set -euo pipefail

OHCAR_CONTAINERS=(nifty_dirac sharp_maxwell)

ensure_ohcar_deploy_idle() {
  command -v docker >/dev/null 2>&1 || {
    printf 'deployment requires docker\n' >&2
    return 1
  }

  local container processes
  for container in "${OHCAR_CONTAINERS[@]}"; do
    docker inspect "$container" >/dev/null 2>&1 || {
      printf 'required container is missing: %s\n' "$container" >&2
      return 1
    }
    if [[ "$(docker inspect -f '{{.State.Running}}' "$container")" != "true" ]]; then
      continue
    fi
    processes="$(docker top "$container" -eo pid,comm)"
    if printf '%s\n' "$processes" | awk 'NR > 1 && $2 != "bash" { found = 1 } END { exit found ? 0 : 1 }'; then
      printf 'refusing deployment: container %s has active non-shell processes\n' "$container" >&2
      return 1
    fi
  done
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  ensure_ohcar_deploy_idle
  printf 'deployment preflight passed\n'
fi
