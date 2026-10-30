#!/usr/bin/env bash
set -euo pipefail
config_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
exec docker compose --project-name ssu-lms \
  --env-file "$config_dir/.env" -f "$config_dir/compose.yml" "$@"
