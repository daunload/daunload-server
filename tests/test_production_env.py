import importlib.util
import os
from pathlib import Path
import subprocess
import sys

from cryptography.fernet import Fernet
import pytest

VALIDATOR = Path(__file__).resolve().parents[1] / "deploy/hetzner/validate_env.py"
spec = importlib.util.spec_from_file_location("production_env", VALIDATOR)
validator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(validator)


@pytest.fixture
def production_env(tmp_path, monkeypatch):
    key = Fernet.generate_key().decode()
    previous = tmp_path / "previous.env"
    previous.write_text(f'FERNET_KEY={key}\nPOSTGRES_PASSWORD=existing-database-password\n')
    monkeypatch.setenv("FERNET_KEY", key)
    monkeypatch.setenv("POSTGRES_PASSWORD", "existing-database-password")
    monkeypatch.setenv("JWT_SECRET", "test-production-secret-32-characters-or-more")
    monkeypatch.setenv("ADMIN_STUDENT_IDS", '["20260001"]')
    return previous


def test_regular_settings_can_change_with_existing_keys(production_env, monkeypatch):
    monkeypatch.setenv("ADMIN_STUDENT_IDS", '["20260002"]')
    monkeypatch.setenv("MAX_AUDIO_STREAMS", "2")
    validator.validate_environment(production_env)


@pytest.mark.parametrize("key", ["FERNET_KEY", "POSTGRES_PASSWORD"])
def test_data_keys_require_a_separate_migration(production_env, monkeypatch, key):
    monkeypatch.setenv(key, Fernet.generate_key().decode() if key == "FERNET_KEY" else "changed-password")
    with pytest.raises(validator.SecretMigrationRequired):
        validator.validate_environment(production_env)


def test_invalid_values_do_not_appear_in_validation_logs(production_env, monkeypatch):
    sentinel = "private-value-that-must-not-appear"
    monkeypatch.setenv("ADMIN_STUDENT_IDS", sentinel)
    result = subprocess.run([sys.executable, str(VALIDATOR), str(production_env)],
                            env=os.environ.copy(), capture_output=True, text=True)
    assert result.returncode != 0
    assert "PRODUCTION_ENV validation failed" in result.stderr
    assert sentinel not in result.stdout + result.stderr
    assert os.environ["FERNET_KEY"] not in result.stdout + result.stderr
