import copy
import importlib.util
import io
import json
from pathlib import Path
import urllib.error

import pytest

SOURCE = Path(__file__).resolve().parents[1] / "deploy/hetzner/firewall.py"
spec = importlib.util.spec_from_file_location("firewall", SOURCE)
fw = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fw)

MARKER = "github-actions:daunload/daunload-server:123:1"
STATIC_RULES = [
    {"direction": "in", "protocol": "tcp", "port": "443", "source_ips": ["0.0.0.0/0"], "description": "HTTPS"},
    {"direction": "in", "protocol": "tcp", "port": "22", "source_ips": ["1.1.1.1/32"], "description": "admin"},
    {"direction": "in", "protocol": "icmp", "port": None, "source_ips": ["0.0.0.0/0"], "destination_ips": [], "description": None},
]


class FakeApi:
    def __init__(self):
        self.current = copy.deepcopy(STATIC_RULES)
        self.writes = []

    def rules(self, firewall_id):
        assert firewall_id == 123
        return copy.deepcopy(self.current)

    def set_rules(self, firewall_id, rules):
        assert firewall_id == 123
        self.writes.append(copy.deepcopy(rules))
        self.current = copy.deepcopy(rules)


def test_only_current_runner_is_allowed_and_existing_rules_are_preserved(tmp_path):
    state = tmp_path / "lease.json"
    api = FakeApi()
    fw.open_rule(api, 123, "8.8.8.8", 22, MARKER, state)
    assert api.current[:-1] == STATIC_RULES
    assert api.current[-1]["source_ips"] == ["8.8.8.8/32"]
    assert api.current[-1]["port"] == "22"
    assert state.stat().st_mode & 0o777 == 0o600
    assert json.loads(state.read_text())["firewall_id"] == 123


def test_cleanup_preserves_rules_added_or_edited_during_deployment(tmp_path):
    state = tmp_path / "lease.json"
    api = FakeApi()
    fw.open_rule(api, 123, "8.8.8.8", 2222, MARKER, state)
    lease = api.current[-1]
    # API responses may fill optional fields absent in the original request.
    lease["destination_ips"] = []
    other_run = {**lease, "description": MARKER + "-another-run"}
    other_admin = {**STATIC_RULES[1], "source_ips": ["9.9.9.9/32"]}
    api.current.extend([other_run, other_admin])
    api.current[0]["description"] = "edited while deploying"
    expected = [rule for rule in api.current if rule is not lease]
    fw.close_rule(api, state)
    assert api.current == expected
    assert not state.exists()


def test_cleanup_can_recover_when_add_response_is_lost(tmp_path):
    state = tmp_path / "lease.json"
    api = FakeApi()
    original_write = api.set_rules

    def lost_response(firewall_id, rules):
        original_write(firewall_id, rules)
        raise fw.ApiError(500)

    api.set_rules = lost_response
    with pytest.raises(fw.ApiError):
        fw.open_rule(api, 123, "8.8.8.8", 22, MARKER, state)
    assert state.exists()
    api.set_rules = original_write
    fw.close_rule(api, state)
    assert api.current == STATIC_RULES


def test_locked_update_retries_with_fresh_rules(tmp_path, monkeypatch):
    api = FakeApi()
    original_write = api.set_rules
    concurrent_rule = {**STATIC_RULES[0], "port": "80"}
    attempts = []

    def locked_once(firewall_id, rules):
        attempts.append(1)
        if len(attempts) == 1:
            api.current.append(concurrent_rule)
            raise fw.ApiError(423)
        original_write(firewall_id, rules)

    api.set_rules = locked_once
    monkeypatch.setattr(fw.time, "sleep", lambda _: None)
    fw.open_rule(api, 123, "8.8.8.8", 22, MARKER, tmp_path / "lease.json")
    assert concurrent_rule in api.current
    assert len(attempts) == 2


def test_repeated_open_and_cleanup_do_not_duplicate_or_remove_other_rules(tmp_path):
    api = FakeApi()
    state = tmp_path / "lease.json"
    fw.open_rule(api, 123, "8.8.8.8", 22, MARKER, state)
    fw.open_rule(api, 123, "8.8.8.8", 22, MARKER, state)
    assert len(api.writes) == 1
    fw.close_rule(api, state)
    # A second close after a failed cleanup response can also safely find no rule.
    state.write_text(json.dumps({"firewall_id": 123, "rule": {**STATIC_RULES[1], "description": MARKER}}))
    fw.close_rule(api, state)
    assert api.current == STATIC_RULES


@pytest.mark.parametrize(("ip", "port"), [("127.0.0.1", 22), ("10.0.0.1", 22), ("::1", 22), ("8.8.8.8", 0)])
def test_invalid_lease_is_rejected_without_writing_rules(tmp_path, ip, port):
    api = FakeApi()
    state = tmp_path / "lease.json"
    with pytest.raises(fw.FirewallError):
        fw.open_rule(api, 123, ip, port, MARKER, state)
    assert not state.exists() and not api.writes


def test_http_errors_do_not_expose_tokens_or_response_bodies(monkeypatch):
    token = "sensitive-test-token"

    def fail(request, timeout):
        assert request.headers["Authorization"] == "Bearer " + token
        raise urllib.error.HTTPError(request.full_url, 403, token, {}, io.BytesIO(token.encode()))

    monkeypatch.setattr(fw.urllib.request, "urlopen", fail)
    with pytest.raises(fw.ApiError) as error:
        fw.Hetzner(token).rules(123)
    assert token not in str(error.value)
    assert "403" in str(error.value)


def test_firewall_action_is_finished_before_open_returns(monkeypatch):
    api = fw.Hetzner("test-token")
    requests = []

    def request(method, path, body=None):
        requests.append((method, path))
        if method == "POST":
            return {"actions": [{"id": 42, "status": "running"}]}
        return {"action": {"id": 42, "status": "success"}}

    monkeypatch.setattr(api, "request", request)
    monkeypatch.setattr(fw.time, "sleep", lambda _: None)
    api.set_rules(123, STATIC_RULES)
    assert requests == [("POST", "/firewalls/123/actions/set_rules"), ("GET", "/firewalls/123/actions/42")]


def test_cleanup_without_lease_does_not_require_api_token(tmp_path, monkeypatch):
    monkeypatch.delenv("HETZNER_API_TOKEN", raising=False)
    monkeypatch.setattr(fw.sys, "argv", [str(SOURCE), "close", "--state", str(tmp_path / "absent")])
    fw.main()
