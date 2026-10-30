#!/usr/bin/env bash
# Run as root. Writes two files per run so the database never travels with its decryption key:
#   ssu-lms-<time>.tar.gz          DB dump, compose.yml, Caddyfile, .release.env
#   ssu-lms-secrets-<time>.env     production .env (FERNET_KEY decrypts stored school passwords)
# Copy them off-server to different places. If BACKUP_AGE_RECIPIENTS names an age recipients file
# (default /etc/ssu-lms/backup-age-recipients), both are encrypted with age and only *.age files are kept.
set -euo pipefail
umask 077
config_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
backup_dir="${BACKUP_DIR:-/var/backups/ssu-lms}"
recipients="${BACKUP_AGE_RECIPIENTS:-/etc/ssu-lms/backup-age-recipients}"
encrypt=false
if [[ -f "$recipients" ]]; then
  command -v age > /dev/null || { echo "age is required when $recipients exists." >&2; exit 1; }
  encrypt=true
fi
install -d -m 700 "$backup_dir"
staging_dir="$(mktemp -d "$backup_dir/.tmp.XXXXXX")"
trap 'rm -rf -- "$staging_dir"' EXIT
"$config_dir/compose.sh" exec -T postgres pg_dump -U lms -d lms -Fc > "$staging_dir/database.dump"
# Verify the dump can be read before publishing it.
"$config_dir/compose.sh" exec -T postgres pg_restore --list < "$staging_dir/database.dump" > /dev/null
cp "$config_dir/compose.yml" "$config_dir/Caddyfile" "$staging_dir/"
backup_files=(database.dump compose.yml Caddyfile)
if [[ -f "$config_dir/.release.env" ]]; then
  cp "$config_dir/.release.env" "$staging_dir/"
  backup_files+=(.release.env)
fi
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
tar -C "$staging_dir" -czf "$staging_dir/backup.tar.gz" "${backup_files[@]}"
install -m 600 "$config_dir/.env" "$staging_dir/secrets.env"

if [[ "$encrypt" == true ]]; then  # Encrypt both before publishing either, so a failure leaves nothing behind.
  age -R "$recipients" -o "$staging_dir/backup.tar.gz.age" "$staging_dir/backup.tar.gz"
  age -R "$recipients" -o "$staging_dir/secrets.env.age" "$staging_dir/secrets.env"
  mv "$staging_dir/backup.tar.gz.age" "$backup_dir/ssu-lms-$stamp.tar.gz.age"
  mv "$staging_dir/secrets.env.age" "$backup_dir/ssu-lms-secrets-$stamp.env.age"
  printf 'Backup saved: %s\n' "$backup_dir/ssu-lms-$stamp.tar.gz.age" "$backup_dir/ssu-lms-secrets-$stamp.env.age"
else
  mv "$staging_dir/backup.tar.gz" "$backup_dir/ssu-lms-$stamp.tar.gz"
  mv "$staging_dir/secrets.env" "$backup_dir/ssu-lms-secrets-$stamp.env"
  printf 'Backup saved: %s\n' "$backup_dir/ssu-lms-$stamp.tar.gz" "$backup_dir/ssu-lms-secrets-$stamp.env"
fi
# Retain 14 days of this script's local backups; off-server retention is separate.
find "$backup_dir" -maxdepth 1 -type f -name 'ssu-lms-*' -mtime +13 -delete
