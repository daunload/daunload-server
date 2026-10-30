#!/usr/bin/env bash
set -euo pipefail
config_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
app_env_file="${APP_ENV_FILE:-$config_dir/.env}"
# Read only the image value, without executing a state file as shell code.
# Use one --env-file so existing Compose installations remain compatible.
if [[ -z "${APP_IMAGE:-}" && -f "$config_dir/.release.env" ]]; then
  APP_IMAGE="$(sed -n 's/^APP_IMAGE=//p' "$config_dir/.release.env")"
  : "${APP_IMAGE:?Missing APP_IMAGE in .release.env}"
  export APP_IMAGE
fi
exec docker compose --project-name ssu-lms \
  --env-file "$app_env_file" -f "$config_dir/compose.yml" "$@"
