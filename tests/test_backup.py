"""backup.sh: DB 백업과 암호화 키(.env)를 따로 저장하고, 수신자 파일이 있으면 age로 암호화한다."""
import os
from pathlib import Path
import subprocess
import tarfile

import pytest

DEPLOY = Path(__file__).resolve().parents[1] / "deploy" / "hetzner"


@pytest.fixture
def backup(tmp_path):
    config = tmp_path / "deploy"
    config.mkdir()
    (config / "backup.sh").write_text((DEPLOY / "backup.sh").read_text())
    (config / "compose.sh").write_text(
        '#!/usr/bin/env bash\n'
        'if [[ " $* " == *" pg_dump "* ]]; then printf "DUMP"; else cat > /dev/null; fi\n')
    (config / "compose.sh").chmod(0o755)
    (config / ".env").write_text("FERNET_KEY=secret-key\n")
    (config / "compose.yml").write_text("services: {}\n")
    (config / "Caddyfile").write_text("example.com\n")
    tools = tmp_path / "bin"
    tools.mkdir()
    out = tmp_path / "backups"
    env = dict(os.environ, BACKUP_DIR=str(out), PATH=f"{tools}:{os.environ['PATH']}")

    def run(**extra):
        return subprocess.run(["bash", str(config / "backup.sh")], env={**env, **extra}, capture_output=True, text=True)

    return out, tools, run


def test_database_archive_does_not_contain_the_encryption_key(backup):
    out, _, run = backup
    result = run()
    assert result.returncode == 0, result.stderr
    [archive] = out.glob("ssu-lms-2*.tar.gz")
    with tarfile.open(archive) as tar:
        names = tar.getnames()
        assert "database.dump" in names and "app.env" not in names
        assert all(b"secret-key" not in tar.extractfile(m).read() for m in tar.getmembers() if m.isfile())
    [secrets] = out.glob("ssu-lms-secrets-*.env")
    assert secrets.read_text() == "FERNET_KEY=secret-key\n"
    assert secrets.stat().st_mode & 0o777 == 0o600


def test_age_recipients_encrypt_both_outputs(backup, tmp_path):
    out, tools, run = backup
    age = tools / "age"
    age.write_text('#!/usr/bin/env bash\n'  # -R 파일 -o 출력 입력
                   'printf "AGE:%s:" "$2" > "$4"; cat "$5" >> "$4"\n')
    age.chmod(0o755)
    recipients = tmp_path / "recipients.txt"
    recipients.write_text("age1example\n")
    result = run(BACKUP_AGE_RECIPIENTS=str(recipients))
    assert result.returncode == 0, result.stderr
    names = sorted(p.name for p in out.iterdir() if not p.name.startswith("."))
    assert len(names) == 2 and all(n.endswith(".age") for n in names), names
    for p in out.glob("*.age"):
        assert p.read_bytes().startswith(f"AGE:{recipients}:".encode())


def test_missing_age_fails_without_leaving_plaintext(backup, tmp_path):
    out, _, run = backup
    recipients = tmp_path / "recipients.txt"
    recipients.write_text("age1example\n")
    result = run(BACKUP_AGE_RECIPIENTS=str(recipients), PATH="/usr/bin:/bin")
    assert result.returncode != 0
    assert not out.exists() or [p for p in out.iterdir() if not p.name.startswith(".")] == []
