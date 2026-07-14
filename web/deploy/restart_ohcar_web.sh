#!/usr/bin/env bash
set -euo pipefail

exec systemctl restart ohcar-web.service
