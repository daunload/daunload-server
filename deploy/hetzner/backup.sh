#!/usr/bin/env bash
# Run as root. Contains database data and encryption keys; keep off-server copies private.
set -euo pipefail
umask 077
config_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
backup_dir=/var/backups/ssu-lms
install -d -m 700 "$backup_dir"
staging_dir="$(mktemp -d "$backup_dir/.tmp.XXXXXX")"
trap 'rm -rf -- "$staging_dir"' EXIT
"$config_dir/compose.sh" exec -T postgres pg_dump -U lms -d lms -Fc > "$staging_dir/database.dump"
# Verify the dump can be read before publishing it.
"$config_dir/compose.sh" exec -T postgres pg_restore --list < "$staging_dir/database.dump" > /dev/null
install -m 600 "$config_dir/.env" "$staging_dir/app.env"
cp "$config_dir/compose.yml" "$config_dir/Caddyfile" "$staging_dir/"
archive="$backup_dir/ssu-lms-$(date -u +%Y%m%dT%H%M%SZ).tar.gz"
tar -C "$staging_dir" -czf "$staging_dir/backup.tar.gz" database.dump app.env compose.yml Caddyfile
mv "$staging_dir/backup.tar.gz" "$archive"
# Retain 14 days of this script's local archives; off-server retention is separate.
find "$backup_dir" -maxdepth 1 -type f -name 'ssu-lms-*.tar.gz' -mtime +13 -delete
printf 'Backup saved: %s\n' "$archive"
