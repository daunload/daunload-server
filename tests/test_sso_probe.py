import json

import pytest

from app.lms import auth
from app.lms.sso_probe import observe, renew, sso_cookies


def test_only_sso_cookies_are_kept():
    cookies = [{"domain": domain, "name": "x", "value": "SECRET"}
               for domain in (".ssu.ac.kr", "smartid.ssu.ac.kr", "canvas.ssu.ac.kr",
                              "lms.ssu.ac.kr", "evilssu.ac.kr")]
    assert [c["domain"] for c in sso_cookies(cookies)] == [".ssu.ac.kr", "smartid.ssu.ac.kr"]


async def test_missing_sso_never_logs_in(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("School login must never be called")
    monkeypatch.setattr(auth, "login", forbidden)
    result, _ = await renew("PRIVATE_USER", [])
    assert result == {"state": "missing_sso_token"}


async def test_observation_preserves_rotation_stops_and_redacts(tmp_path, monkeypatch, capsys):
    seen = []
    async def fake_renew(user, cookies):
        seen.append(cookies[0]["value"])
        if len(seen) == 1:
            return {"state": "renewed", "sso_token_changed": True}, [
                {"name": "sToken", "value": "NEW_SECRET", "domain": ".ssu.ac.kr"}]
        raise RuntimeError("DO_NOT_LOG_SECRET")
    monkeypatch.setattr("app.lms.sso_probe.renew", fake_renew)
    session = auth.LmsSession("PRIVATE_USER", "API_SECRET", [
        {"name": "sToken", "value": "OLD_SECRET", "domain": ".ssu.ac.kr"}])
    output = tmp_path / "result.jsonl"
    await observe(session, output, [0, 0, 0])
    assert seen == ["OLD_SECRET", "NEW_SECRET"]
    records = [json.loads(line) for line in output.read_text().splitlines()]
    assert len(records) == 2
    assert not records[0]["original_sso_token_still_used"]
    assert records[1]["state"] == "probe_error"
    assert output.stat().st_mode & 0o777 == 0o600
    assert "SECRET" not in output.read_text() + capsys.readouterr().out
    assert "PRIVATE_USER" not in output.read_text()
