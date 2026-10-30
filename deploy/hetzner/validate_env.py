"""Validate candidate production settings inside the newly built app image."""
import os
from pathlib import Path
import sys

from cryptography.fernet import Fernet
from dotenv import dotenv_values

from app.config import Settings


class SecretMigrationRequired(Exception):
    pass


def validate_environment(previous: Path) -> None:
    settings = Settings(_env_file=None)
    Fernet(settings.fernet_key)
    if settings.jwt_secret == "change-me" or len(settings.jwt_secret) < 32:
        raise ValueError("Invalid JWT_SECRET")
    old = dotenv_values(previous, interpolate=False)
    for key in ("FERNET_KEY", "POSTGRES_PASSWORD"):
        if old.get(key) and old[key] != os.environ.get(key):
            raise SecretMigrationRequired(key)


if __name__ == "__main__":
    try:
        validate_environment(Path(sys.argv[1]))
    except SecretMigrationRequired as exc:
        print(f"{exc} requires a separate key/database migration; automatic change refused.", file=sys.stderr)
        sys.exit(1)
    except Exception:
        # Pydantic exceptions can include sensitive input values. Never print them.
        print("PRODUCTION_ENV validation failed. Check required keys and value formats; no values are logged.", file=sys.stderr)
        sys.exit(1)
