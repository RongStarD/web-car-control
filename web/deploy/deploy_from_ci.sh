#!/usr/bin/env bash
set -euo pipefail

SOURCE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TARGET_ROOT="${OHCAR_DEPLOY_ROOT:-/home/jetson/ohcar}"

source "$SOURCE_ROOT/web/deploy/deploy_guard.sh"

[[ -f "$SOURCE_ROOT/web/frontend/dist/index.html" ]] || {
  printf 'frontend artifact is missing\n' >&2
  exit 1
}
command -v rsync >/dev/null 2>&1 || {
  printf 'deployment requires rsync\n' >&2
  exit 1
}

ensure_ohcar_deploy_idle
install -d -m 0755 "$TARGET_ROOT"

if [[ "$(readlink -f "$SOURCE_ROOT")" == "$(readlink -f "$TARGET_ROOT")" ]]; then
  printf 'CI checkout and stable deployment directory must be different\n' >&2
  exit 1
fi

rsync -a --delete \
  --exclude='.git/' \
  --exclude='.agents/' \
  --exclude='.venv/' \
  --exclude='*.tar.gz' \
  --exclude='web/frontend/node_modules/' \
  --exclude='web/frontend/dist/' \
  "$SOURCE_ROOT/" "$TARGET_ROOT/"

install -d -m 0755 "$TARGET_ROOT/web/frontend/dist"
rsync -a --delete \
  "$SOURCE_ROOT/web/frontend/dist/" \
  "$TARGET_ROOT/web/frontend/dist/"

bash "$TARGET_ROOT/web/deploy/install_on_jetson.sh" --runtime-only
curl --fail --silent --show-error --retry 6 --retry-delay 2 \
  http://127.0.0.1:8080/api/bootstrap >/dev/null

printf 'deployed commit %s to %s\n' "${GITHUB_SHA:-unknown}" "$TARGET_ROOT"
