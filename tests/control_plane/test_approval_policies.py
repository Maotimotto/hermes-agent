"""Tests for ApprovalGate policy chain (risk inference)."""

from __future__ import annotations

import pytest

from agent.control_plane.approval.policies import (
    DEFAULT_POLICIES,
    evaluate_policies,
)
from agent.control_plane.approval.types import ApprovalRequest, RiskLevel


def _req(action_kind: str, **payload) -> ApprovalRequest:
    return ApprovalRequest(action_kind=action_kind, action_payload=payload or None)


class TestDefaultPolicies:
    # ── destructive (critical) ──────────────────────────────────────────────

    @pytest.mark.parametrize(
        "cmd",
        [
            "rm -rf /tmp/x",
            "git push --force origin main",
            "git push -f",
            "DROP TABLE users",
            "DROP DATABASE prod",
            "TRUNCATE TABLE sessions",
            "mkfs.ext4 /dev/sda",
            "dd if=/dev/zero of=/dev/sda",
            "shutdown -h now",
            "reboot",
        ],
    )
    def test_destructive(self, cmd: str) -> None:
        risk, _warn, name = evaluate_policies(_req("shell", command=cmd))
        assert risk == RiskLevel.CRITICAL
        assert name == "destructive"

    # ── privileged (high) ───────────────────────────────────────────────────

    @pytest.mark.parametrize(
        "cmd",
        [
            "sudo apt install foo",
            "chmod 777 /etc/hosts",
            "chown root:root /tmp",
            "kill -9 1234",
        ],
    )
    def test_privileged(self, cmd: str) -> None:
        risk, _warn, name = evaluate_policies(_req("shell", command=cmd))
        assert risk == RiskLevel.HIGH
        assert name == "privileged"

    # ── secret (high) ───────────────────────────────────────────────────────

    def test_secret_by_kind(self) -> None:
        risk, _, name = evaluate_policies(_req("secret", path="~/.aws/credentials"))
        assert risk == RiskLevel.HIGH
        assert name == "secret"

    @pytest.mark.parametrize(
        "cmd",
        ["cat .env", "echo $API_KEY", "grep password config.yml"],
    )
    def test_secret_by_command(self, cmd: str) -> None:
        risk, _, name = evaluate_policies(_req("shell", command=cmd))
        assert risk == RiskLevel.HIGH
        assert name == "secret"

    # ── network (medium) ────────────────────────────────────────────────────

    @pytest.mark.parametrize(
        "cmd",
        ["curl https://example.com", "wget http://x.com/y.zip"],
    )
    def test_network(self, cmd: str) -> None:
        risk, _, name = evaluate_policies(_req("shell", command=cmd))
        assert risk == RiskLevel.MEDIUM
        assert name == "network"

    def test_network_by_kind(self) -> None:
        risk, _, name = evaluate_policies(_req("network", url="https://api.example.com"))
        assert risk == RiskLevel.MEDIUM
        assert name == "network"

    # ── file_write (medium) ─────────────────────────────────────────────────

    def test_file_write(self) -> None:
        risk, _, name = evaluate_policies(_req("file_write", path="/tmp/safe.txt"))
        assert risk == RiskLevel.MEDIUM
        assert name == "file_write"

    # ── shell low ───────────────────────────────────────────────────────────

    @pytest.mark.parametrize("cmd", ["ls -la", "pwd", "echo hello"])
    def test_shell_low(self, cmd: str) -> None:
        risk, _, name = evaluate_policies(_req("shell", command=cmd))
        assert risk == RiskLevel.LOW
        assert name == "shell"

    # ── unmatched ───────────────────────────────────────────────────────────

    def test_unknown_kind_falls_through(self) -> None:
        risk, warn, name = evaluate_policies(_req("totally_unknown_kind"))
        assert risk == RiskLevel.LOW
        assert warn == ""
        assert name == "default"

    # ── ordering: destructive beats privileged beats secret ────────────────

    def test_priority_destructive_over_privileged(self) -> None:
        # `sudo rm -rf` is BOTH privileged and destructive — destructive wins.
        risk, _, name = evaluate_policies(_req("shell", command="sudo rm -rf /"))
        assert risk == RiskLevel.CRITICAL
        assert name == "destructive"


class TestPolicyResilience:
    def test_bad_policy_does_not_break_chain(self) -> None:
        from agent.control_plane.approval.policies import ApprovalPolicy

        def boom(_req):
            raise RuntimeError("simulated buggy policy")

        custom = [
            ApprovalPolicy(name="buggy", match=boom, risk=RiskLevel.CRITICAL),
            *DEFAULT_POLICIES,
        ]
        # Should skip the buggy one and still classify normally.
        risk, _, name = evaluate_policies(_req("shell", command="rm -rf /tmp"), custom)
        assert risk == RiskLevel.CRITICAL
        assert name == "destructive"
