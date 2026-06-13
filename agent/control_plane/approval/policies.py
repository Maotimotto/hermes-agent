"""Approval policies — risk inference rules.

Five default policies, ordered from most-specific to least-specific.
First match wins. Each policy returns a RiskLevel + a human-readable
warning message.

The policies inspect the ``ApprovalRequest.action_kind`` +
``action_payload`` (which carries ``command`` / ``path`` / ``url`` etc.)
and never block on their own — blocking is the caller's decision based
on the returned risk level and warning.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .types import ApprovalRequest, RiskLevel


@dataclass
class ApprovalPolicy:
    name: str
    match: Callable[[ApprovalRequest], bool]
    risk: RiskLevel
    warning: str = ""


# ── helper extractors ───────────────────────────────────────────────────────


def _cmd(req: ApprovalRequest) -> str:
    p = req.action_payload or {}
    return str(p.get("command") or p.get("cmd") or "")


def _path(req: ApprovalRequest) -> str:
    p = req.action_payload or {}
    return str(p.get("path") or p.get("file") or p.get("target") or "")


def _url(req: ApprovalRequest) -> str:
    p = req.action_payload or {}
    return str(p.get("url") or p.get("endpoint") or "")


# ── 1. Destructive (critical) ───────────────────────────────────────────────


_DESTRUCTIVE_NEEDLES = (
    "rm -rf",
    "git push --force",
    "git push -f",
    "DROP TABLE",
    "DROP DATABASE",
    "TRUNCATE TABLE",
    "mkfs",
    "dd if=/dev/zero",
    "shutdown",
    "reboot",
)


def _is_destructive(req: ApprovalRequest) -> bool:
    cmd = _cmd(req)
    return any(needle in cmd for needle in _DESTRUCTIVE_NEEDLES)


# ── 2. Privileged shell (high) ──────────────────────────────────────────────


def _is_privileged(req: ApprovalRequest) -> bool:
    cmd = _cmd(req)
    if not cmd:
        return False
    return (
        cmd.startswith("sudo ")
        or " sudo " in cmd
        or "chmod 777" in cmd
        or cmd.startswith("chown ")
        or " chown " in cmd
        or cmd.startswith("kill ")
        or " kill -9 " in cmd
    )


# ── 3. Secret access (high) ─────────────────────────────────────────────────


_SECRET_NEEDLES = (
    "API_KEY",
    "api_key",
    "secret",
    "token",
    "password",
    "credential",
    ".env",
)


def _is_secret(req: ApprovalRequest) -> bool:
    if req.action_kind == "secret":
        return True
    cmd = _cmd(req)
    path = _path(req)
    haystack = (cmd + " " + path).lower()
    return any(needle.lower() in haystack for needle in _SECRET_NEEDLES)


# ── 4. Network (medium) ─────────────────────────────────────────────────────


def _is_network(req: ApprovalRequest) -> bool:
    if req.action_kind == "network":
        return True
    cmd = _cmd(req)
    if not cmd:
        return False
    return (
        cmd.startswith("curl ")
        or cmd.startswith("wget ")
        or " curl " in cmd
        or " wget " in cmd
        or "http://" in cmd
        or "https://" in cmd
    )


# ── 5. File write (medium) / Shell command (low) ────────────────────────────


def _is_file_write(req: ApprovalRequest) -> bool:
    return req.action_kind in ("file_write", "file.edit", "file")


def _is_shell(req: ApprovalRequest) -> bool:
    return req.action_kind in ("shell", "shell.command")


# ── Default ordered policy chain ────────────────────────────────────────────


DEFAULT_POLICIES: list[ApprovalPolicy] = [
    ApprovalPolicy(
        name="destructive",
        match=_is_destructive,
        risk=RiskLevel.CRITICAL,
        warning="此操作将永久删除数据或破坏环境，不可恢复。",
    ),
    ApprovalPolicy(
        name="privileged",
        match=_is_privileged,
        risk=RiskLevel.HIGH,
        warning="此命令需要特权（sudo/chmod/chown/kill），可能影响系统安全。",
    ),
    ApprovalPolicy(
        name="secret",
        match=_is_secret,
        risk=RiskLevel.HIGH,
        warning="此操作可能涉及密钥、密码或敏感配置文件。",
    ),
    ApprovalPolicy(
        name="network",
        match=_is_network,
        risk=RiskLevel.MEDIUM,
        warning="此操作将访问外部网络。",
    ),
    ApprovalPolicy(
        name="file_write",
        match=_is_file_write,
        risk=RiskLevel.MEDIUM,
        warning="此操作将修改文件。",
    ),
    ApprovalPolicy(
        name="shell",
        match=_is_shell,
        risk=RiskLevel.LOW,
        warning="",
    ),
]


def evaluate_policies(
    req: ApprovalRequest,
    policies: list[ApprovalPolicy] | None = None,
) -> tuple[RiskLevel, str, str]:
    """Walk the policy chain. Return (risk, warning, matched_name).

    If no policy matches, returns (LOW, "", "default").
    """
    chain = policies if policies is not None else DEFAULT_POLICIES
    for p in chain:
        try:
            if p.match(req):
                return p.risk, p.warning, p.name
        except Exception:
            # A buggy policy must never bring the gate down.
            continue
    return RiskLevel.LOW, "", "default"
