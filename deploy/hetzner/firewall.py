"""Lease one runner's SSH rule, preserving all unrelated Hetzner firewall rules."""
import argparse
import ipaddress
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request


class FirewallError(Exception):
    """Only sanitized, non-secret messages may be passed to this exception."""


class ApiError(FirewallError):
    def __init__(self, status):
        self.status = status
        super().__init__(f"Hetzner API returned HTTP {status}; check token permissions and firewall ID.")


class Hetzner:
    def __init__(self, token):
        if not token:
            raise FirewallError("Set the production secret HETZNER_API_TOKEN (Read & Write).")
        self.token = token

    def request(self, method, path, body=None):
        request = urllib.request.Request(
            "https://api.hetzner.cloud/v1" + path,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"Authorization": "Bearer " + self.token, "Content-Type": "application/json"},
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            # Do not print response bodies, headers, or exception representations.
            raise ApiError(exc.code) from None
        except (urllib.error.URLError, TimeoutError, ValueError):
            raise FirewallError("Hetzner API could not be reached or returned an invalid response.") from None

    def rules(self, firewall_id):
        return self.request("GET", f"/firewalls/{firewall_id}")["firewall"]["rules"]

    def set_rules(self, firewall_id, rules):
        response = self.request("POST", f"/firewalls/{firewall_id}/actions/set_rules", {"rules": rules})
        for action in response["actions"]:
            deadline = time.monotonic() + 120
            while action["status"] == "running":
                if time.monotonic() >= deadline:
                    raise FirewallError("Timed out waiting for the firewall change; cleanup will still be attempted.")
                time.sleep(2)
                action = self.request("GET", f"/firewalls/{firewall_id}/actions/{int(action['id'])}")["action"]
            if action["status"] != "success":
                raise FirewallError("Hetzner could not apply the firewall change.")


def matches(rule, lease):
    return all(rule.get(key) == lease[key] for key in ("direction", "protocol", "port", "source_ips", "description"))


def update_rule(api, firewall_id, lease, *, remove=False):
    for attempt in range(4):
        # Re-read on every attempt and at cleanup, never restore a stale snapshot.
        current = api.rules(firewall_id)
        if remove:
            new = [rule for rule in current if not matches(rule, lease)]
        elif any(matches(rule, lease) for rule in current):
            return
        else:
            new = [*current, lease]
        if new == current:
            return
        try:
            api.set_rules(firewall_id, new)
            return
        except ApiError as exc:
            if exc.status not in (409, 423, 429) or attempt == 3:
                raise
            time.sleep(3)


def open_rule(api, firewall_id, ip, port, marker, state):
    address = ipaddress.ip_address(ip)
    if address.version != 4 or not address.is_global:
        raise FirewallError("The runner must have a public IPv4 address.")
    if not 1 <= port <= 65535:
        raise FirewallError("DEPLOY_PORT must be between 1 and 65535.")
    lease = {"direction": "in", "protocol": "tcp", "port": str(port),
             "source_ips": [str(address) + "/32"], "description": marker}
    # Record first, so cleanup can remove a rule even if the write response is lost.
    state.write_text(json.dumps({"firewall_id": firewall_id, "rule": lease}))
    state.chmod(0o600)
    update_rule(api, firewall_id, lease)


def close_rule(api, state):
    saved = json.loads(state.read_text())
    update_rule(api, int(saved["firewall_id"]), saved["rule"], remove=True)
    state.unlink()


def runner_ip():
    try:
        result = subprocess.run(["curl", "-4", "--fail", "--silent", "--show-error", "--max-time", "20",
                                 "https://ip.hetzner.com"], capture_output=True, text=True, check=True)
        return result.stdout.strip()
    except subprocess.SubprocessError:
        raise FirewallError("Could not determine the runner's public IPv4 address.") from None


def wait_for_ssh(host, port):
    for _ in range(12):
        try:
            with socket.create_connection((host, port), timeout=5):
                return
        except OSError:
            time.sleep(2)
    raise FirewallError("SSH is still unreachable. Check DEPLOY_HOST, DEPLOY_PORT, UFW, and the firewall attachment.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=("open", "close"))
    parser.add_argument("--state", type=Path, required=True)
    args = parser.parse_args()
    if args.operation == "close" and not args.state.exists():
        print("No temporary firewall rule was created.")
        return
    api = Hetzner(os.environ.get("HETZNER_API_TOKEN", ""))
    if args.operation == "close":
        close_rule(api, args.state)
        print("Removed this run's temporary SSH rule.")
        return
    firewall_id = int(os.environ.get("HETZNER_FIREWALL_ID", "0"))
    if firewall_id <= 0:
        raise FirewallError("Set the production variable HETZNER_FIREWALL_ID.")
    port = int(os.environ.get("DEPLOY_PORT", "22"))
    marker = "github-actions:{repo}:{run}:{attempt}".format(
        repo=os.environ["GITHUB_REPOSITORY"], run=os.environ["GITHUB_RUN_ID"],
        attempt=os.environ["GITHUB_RUN_ATTEMPT"],
    )
    open_rule(api, firewall_id, runner_ip(), port, marker, args.state)
    wait_for_ssh(os.environ["DEPLOY_HOST"], port)
    print("Temporary runner-only SSH access is ready.")


if __name__ == "__main__":
    try:
        main()
    except FirewallError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
    except Exception:
        print("Firewall operation failed. Check configuration; credentials and response bodies are not logged.", file=sys.stderr)
        sys.exit(1)
