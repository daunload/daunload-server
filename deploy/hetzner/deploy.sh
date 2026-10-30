#!/usr/bin/env bash
# Called by CI as root. GHCR password is supplied over stdin, never as an argument.
set -euo pipefail
umask 077
config_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
image="${1:?image digest required}"
registry_user="${2:?GitHub registry user required}"
env_name="${3:-}"
pending_env=""
if [[ -n "$env_name" ]]; then
  if [[ ! "$env_name" =~ ^\.env\.ci-[0-9]+-[0-9]+$ ]]; then
    echo "Invalid environment upload name." >&2
    exit 1
  fi
  pending_env="$config_dir/$env_name"
fi
if [[ ! "$image" =~ ^ghcr\.io/[a-z0-9._/-]+@sha256:[a-f0-9]{64}$ ]]; then
  echo "Expected an immutable GHCR image digest." >&2
  exit 1
fi
if [[ "$EUID" -ne 0 ]]; then
  echo "Run this script with sudo -n." >&2
  exit 1
fi
exec 9>"$config_dir/.deploy.lock"
flock -n 9 || { echo "Another deployment is running." >&2; exit 1; }
registry_config="$(mktemp -d)"
cleanup() {
  rm -rf -- "$registry_config"
  if [[ -n "$pending_env" ]]; then rm -f -- "$pending_env"; fi
}
trap cleanup EXIT
export DOCKER_CONFIG="$registry_config"
export APP_IMAGE="$image"
dc() { bash "$config_dir/compose.sh" "$@"; }

docker login ghcr.io --username "$registry_user" --password-stdin
dc config --quiet
# Download before stopping anything. This also works with private GHCR packages.
dc pull api
if [[ -n "$pending_env" ]]; then
  test -s "$pending_env"
  chmod 600 "$pending_env"
  if ! APP_ENV_FILE="$pending_env" dc config --quiet >/dev/null 2>&1; then
    echo "PRODUCTION_ENV has missing or invalid Compose settings. Existing settings are unchanged." >&2
    exit 1
  fi
  # Validate with the new image and Compose's dotenv handling, without logging values.
  APP_ENV_FILE="$pending_env" dc run --rm --no-deps \
    -v "$config_dir/.env:/run/previous.env:ro" api \
    python /app/deploy/hetzner/validate_env.py /run/previous.env
fi
if [[ -f "$config_dir/.release.env" ]]; then
  cp "$config_dir/.release.env" "$config_dir/.release.env.previous"
else
  # Preserve the original locally loaded image for the first automated deployment.
  container="$(dc ps -q api)"
  if [[ -n "$container" ]]; then
    docker image tag "$(docker inspect --format '{{.Image}}' "$container")" ssu-lms:previous
    printf 'APP_IMAGE=ssu-lms:previous\n' > "$config_dir/.release.env.previous"
  fi
fi
bash "$config_dir/backup.sh"

deployment_failed() {
  echo "Deployment stopped. Check container status and the saved DB backup before retrying." >&2
  echo "No automatic rollback is attempted after a schema migration." >&2
}
trap deployment_failed ERR
# A short interruption prevents old workers from using a changing DB schema.
dc stop caddy worker sync-worker api
if [[ -n "$pending_env" ]]; then
  cp -p "$config_dir/.env" "$config_dir/.env.previous"
  chmod 600 "$config_dir/.env.previous"
  cp -p "$config_dir/.env" "$config_dir/.env.next"
  cat "$pending_env" > "$config_dir/.env.next"
  chmod 600 "$config_dir/.env.next"
  mv "$config_dir/.env.next" "$config_dir/.env"
fi
dc run --rm --no-deps api alembic upgrade head
dc up -d --no-build --no-deps --force-recreate --wait --wait-timeout 180 api
dc up -d --no-build --no-deps --force-recreate --wait --wait-timeout 180 sync-worker worker caddy
dc exec -T api python -c '
import os, sys, time, urllib.request
url = "https://" + os.environ["DOMAIN"] + "/health"
for attempt in range(6):
    try:
        with urllib.request.urlopen(url, timeout=15) as response:
            assert response.status == 200
        break
    except Exception:
        if attempt == 5:
            print("Public HTTPS health check failed.", file=sys.stderr)
            sys.exit(1)
        time.sleep(5)
'

# Persist only a successful image selection; secrets never enter this state file.
printf 'APP_IMAGE=%s\n' "$image" > "$config_dir/.release.env.next"
mv "$config_dir/.release.env.next" "$config_dir/.release.env"
dc ps
echo "Deployed $image"
