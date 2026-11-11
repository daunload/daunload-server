import importlib.util
import os
from pathlib import Path
import subprocess
import sys

from cryptography.fernet import Fernet
import pytest

from app.config import Settings

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
    monkeypatch.setenv("SYNC_JITTER_MINUTES", "10")
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


def test_server_has_no_ffmpeg_audio_path():
    assert "ffmpeg" not in (Path(__file__).resolve().parent.parent / "Dockerfile").read_text()
    assert not hasattr(Settings(), "max_audio_streams")


ROOT = Path(__file__).resolve().parent.parent


def test_dockerfile_playwright_versions_match_lockfile():
    """Chrome 설치용 CLI, 기본 이미지, uv.lock의 Playwright 버전이 함께 움직이게 한다."""
    import re
    import tomllib

    dockerfile = (ROOT / "Dockerfile").read_text()
    locked = next(p["version"] for p in tomllib.loads((ROOT / "uv.lock").read_text())["package"] if p["name"] == "playwright")
    assert re.search(r"^ARG PLAYWRIGHT_VERSION=(\S+)$", dockerfile, re.MULTILINE).group(1) == locked
    assert f"mcr.microsoft.com/playwright/python:v{locked}-" in dockerfile


def test_chrome_layer_is_built_before_dependencies():
    dockerfile = (ROOT / "Dockerfile").read_text()
    assert dockerfile.index("playwright install --with-deps chrome") < dockerfile.index("COPY pyproject.toml uv.lock")


def test_dockerignore_keeps_runtime_files_and_drops_local_clutter():
    ignored = (ROOT / ".dockerignore").read_text().split()
    for pattern in (".claude/", "*.mp4", "**/.venv", "tests/", "docs/", "**/.env"):
        assert pattern in ignored
    # 운영 배포가 이미지 안에서 실행하는 파일은 제외하면 안 된다.
    # (app/web-dist/는 web 단계에서 새로 빌드해 복사하므로 제외해도 된다.)
    assert not any(p.startswith(("deploy", "migrations", "app", "alembic")) for p in ignored if p != "app/web-dist/")
