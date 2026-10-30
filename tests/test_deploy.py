"""Exercise deployment ordering and failure boundaries without a real server."""
import json
import os
from pathlib import Path
import subprocess

import pytest

DEPLOY = Path(__file__).resolve().parents[1] / "deploy" / "hetzner"
IMAGE = "ghcr.io/daunload/daunload-server@sha256:" + "a" * 64
PREVIOUS = "ghcr.io/daunload/daunload-server@sha256:" + "b" * 64


@pytest.fixture
def deployment(tmp_path):
    config = tmp_path / "deploy"
    config.mkdir()
    script = (DEPLOY / "deploy.sh").read_text()
    # Only simulate the root identity check. No root privileges are used by tests.
    script = script.replace('[[ "$EUID" -ne 0 ]]', '[[ "${TEST_EUID:-0}" -ne 0 ]]')
    (config / "deploy.sh").write_text(script)
    (config / "compose.sh").write_text((DEPLOY / "compose.sh").read_text())
    (config / "backup.sh").write_text(
        'printf "backup\\n" >> "$MOCK_ORDER"\n'
        'if [[ "${MOCK_FAIL:-}" == backup ]]; then exit 1; fi\n'
    )
    (config / ".env").write_text("FERNET_KEY=keep-existing-key\nJWT_SECRET=keep-existing-secret\n")
    tools = tmp_path / "bin"
    tools.mkdir()
    docker = tools / "docker"
    docker.write_text('''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
args = sys.argv[1:]
with open(os.environ["MOCK_LOG"], "a") as f:
    f.write(json.dumps(args) + "\\n")
if args[0] == "login":
    sys.stdin.read()
    Path(os.environ["MOCK_AUTH_PATH"]).write_text(os.environ["DOCKER_CONFIG"])
command = next((a for a in args if a in {"login", "pull", "config", "stop", "run", "up"}), "other")
if any(a.endswith("/validate_env.py") for a in args):
    command = "validate-env"
with open(os.environ["MOCK_ORDER"], "a") as f:
    f.write(command + "\\n")
if command == os.environ.get("MOCK_FAIL"):
    sys.exit(1)
if args[0] == "inspect":
    print("sha256:original-image")
if "ps" in args and "-q" in args:
    print("old-api-container")
''')
    docker.chmod(0o755)
    flock = tools / "flock"
    flock.write_text("#!/bin/sh\nexit 0\n")
    flock.chmod(0o755)
    env = dict(os.environ, PATH=f"{tools}:{os.environ['PATH']}",
               MOCK_LOG=str(tmp_path / "docker.log"), MOCK_ORDER=str(tmp_path / "order.log"),
               MOCK_AUTH_PATH=str(tmp_path / "auth-path"))

    def run(image=IMAGE, fail=None, env_name=""):
        run_env = dict(env)
        if fail:
            run_env["MOCK_FAIL"] = fail
        result = subprocess.run(["bash", str(config / "deploy.sh"), image, "test-user", env_name],
                                env=run_env, input="private-registry-token", text=True, capture_output=True)
        log_path = Path(env["MOCK_LOG"])
        calls = [json.loads(line) for line in log_path.read_text().splitlines()] if log_path.exists() else []
        order_path = Path(env["MOCK_ORDER"])
        order = order_path.read_text().splitlines() if order_path.exists() else []
        return result, calls, order

    return config, env, run


def test_success_persists_digest_after_health_checks_and_preserves_secrets(deployment):
    config, env, run = deployment
    before = (config / ".env").read_bytes()
    result, calls, order = run()
    assert result.returncode == 0, result.stderr
    assert order.index("pull") < order.index("backup") < order.index("stop") < order.index("run") < order.index("up")
    assert (config / ".release.env").read_text() == f"APP_IMAGE={IMAGE}\n"
    assert (config / ".release.env.previous").read_text() == "APP_IMAGE=ssu-lms:previous\n"
    assert (config / ".env").read_bytes() == before
    assert not Path(Path(env["MOCK_AUTH_PATH"]).read_text()).exists()
    assert "private-registry-token" not in json.dumps(calls) + result.stdout + result.stderr
    assert any("up" in c and "--wait" in c and "worker" in c and "sync-worker" in c for c in calls)


@pytest.mark.parametrize("failure", ["login", "pull", "backup", "run", "up"])
def test_failures_do_not_record_a_successful_release(deployment, failure):
    config, env, run = deployment
    (config / ".release.env").write_text(f"APP_IMAGE={PREVIOUS}\n")
    result, _, order = run(fail=failure)
    assert result.returncode != 0
    assert (config / ".release.env").read_text() == f"APP_IMAGE={PREVIOUS}\n"
    if failure in ("login", "pull", "backup"):
        assert "stop" not in order
    if failure == "run":
        assert "up" not in order
    assert not Path(Path(env["MOCK_AUTH_PATH"]).read_text()).exists()


def test_success_keeps_previous_registry_digest_for_manual_recovery(deployment):
    config, _, run = deployment
    (config / ".release.env").write_text(f"APP_IMAGE={PREVIOUS}\n")
    result, _, _ = run()
    assert result.returncode == 0, result.stderr
    assert (config / ".release.env.previous").read_text() == f"APP_IMAGE={PREVIOUS}\n"
    assert (config / ".release.env").read_text() == f"APP_IMAGE={IMAGE}\n"


def test_mutable_tag_is_rejected_before_any_docker_operation(deployment):
    _, _, run = deployment
    result, calls, _ = run(image="ghcr.io/daunload/daunload-server:latest")
    assert result.returncode != 0
    assert calls == []


def test_uploaded_environment_is_applied_after_backup_and_stopping_services(deployment):
    config, _, run = deployment
    before = (config / ".env").read_bytes()
    name = ".env.ci-123-1"
    candidate = b'FERNET_KEY=keep-existing-key\nJWT_SECRET=new-secret\nMAX_AUDIO_STREAMS=2\n'
    (config / name).write_bytes(candidate)
    result, calls, order = run(env_name=name)
    assert result.returncode == 0, result.stderr
    assert order.index("validate-env") < order.index("backup") < order.index("stop") < order.index("run")
    assert (config / ".env").read_bytes() == candidate
    assert (config / ".env.previous").read_bytes() == before
    assert (config / ".env").stat().st_mode & 0o777 == 0o600
    assert not (config / name).exists()
    assert "new-secret" not in result.stdout + result.stderr + json.dumps(calls)
    assert all("--force-recreate" in call for call in calls if "up" in call)


@pytest.mark.parametrize("failure", ["validate-env", "backup", "run"])
def test_failed_environment_deploy_preserves_previous_settings_and_cleans_upload(deployment, failure):
    config, _, run = deployment
    before = (config / ".env").read_bytes()
    name = ".env.ci-456-1"
    candidate = b'MAX_AUDIO_STREAMS=2\n'
    (config / name).write_bytes(candidate)
    result, _, order = run(env_name=name, fail=failure)
    assert result.returncode != 0
    assert not (config / name).exists()
    if failure in ("validate-env", "backup"):
        assert (config / ".env").read_bytes() == before
        assert "stop" not in order
    else:
        assert (config / ".env.previous").read_bytes() == before
        assert (config / ".env").read_bytes() == candidate
        assert "up" not in order
