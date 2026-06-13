"""Runtime adapters for ApprovalGate integration.

This module provides the bridge between an :class:`AgentRuntime` tool call
and the :class:`ApprovalGate`.  A runtime calls :func:`request_tool_approval`
before executing a high-risk tool (Bash/Write/Edit/etc.); the helper builds
the :class:`ApprovalRequest`, runs it through the gate, and — when the gate
returns ``pending`` — awaits the user decision.

Both Codex App Server and Claude Agent SDK runtimes wire this in via their
``on_tool_call`` interceptor.  They are otherwise unchanged.
"""

from __future__ import annotations

from typing import Any

from .gate import ApprovalGate
from .types import ApprovalRequest


# ── Category inference (Python port of the TS reference in 07-Approval-Gate.md) ─


_SHELL_TOOLS = {"Bash", "bash", "shell", "exec", "execute", "run_shell_cmd"}
_FILE_TOOLS = {"Write", "write", "Edit", "edit", "patch", "write_file", "patch_file"}


def infer_action_kind(tool_name: str, tool_input: dict[str, Any] | None) -> str:
    """Map a runtime tool call to an ``action_kind`` for the gate."""
    if tool_name in _SHELL_TOOLS:
        return "shell"
    if tool_name in _FILE_TOOLS:
        return "file_write"
    return "tool"


def _extract_payload(
    tool_name: str, tool_input: dict[str, Any] | None
) -> dict[str, Any]:
    """Build a normalised ``action_payload`` from a runtime tool input."""
    if not isinstance(tool_input, dict):
        return {"tool": tool_name, "raw": tool_input}
    out: dict[str, Any] = {"tool": tool_name}
    # Common command field aliases
    for key in ("command", "cmd", "script"):
        if key in tool_input:
            out["command"] = tool_input[key]
            break
    # File-path aliases
    for key in ("path", "file", "file_path", "target"):
        if key in tool_input:
            out["path"] = tool_input[key]
            break
    # Network-style aliases
    for key in ("url", "endpoint"):
        if key in tool_input:
            out["url"] = tool_input[key]
            break
    return out


# ── Public entry point ──────────────────────────────────────────────────────


async def request_tool_approval(
    gate: ApprovalGate,
    *,
    session_id: str,
    turn_id: str | None,
    tool_name: str,
    tool_input: dict[str, Any] | None,
) -> tuple[bool, str]:
    """Ask the gate whether ``tool_name(tool_input)`` should proceed.

    Returns ``(allowed, source)``.  ``allowed=False`` means the runtime
    must abort the tool call; ``source`` is a short string the runtime can
    log/surface (``whitelist`` / ``blacklist`` / ``remembered`` / ``user`` /
    ``policy``).

    For ``pending`` results, this function will *block* until the user (or
    another resolver, e.g. an automated policy) calls
    :meth:`ApprovalGate.resolve`.
    """
    req = ApprovalRequest(
        action_kind=infer_action_kind(tool_name, tool_input),
        action_payload=_extract_payload(tool_name, tool_input),
        session_id=session_id,
        turn_id=turn_id or "",
    )
    result = await gate.evaluate(req)

    if result.decision == "approved":
        return True, result.source
    if result.decision == "denied":
        return False, result.source
    # pending → wait
    if result.awaitable is None:  # pragma: no cover - defensive
        return False, "pending"
    final = await result.awaitable
    return final == "approved", "user"


# ── Sync helper for runtimes that only have a boolean predicate ────────────


def needs_approval(tool_name: str) -> bool:
    """Quick predicate runtimes can use to decide whether to call the gate.

    The reference list comes from the TS spec in 07-Approval-Gate.md §4
    ("Bash and Write/Edit tools always need approval").
    """
    return tool_name in _SHELL_TOOLS or tool_name in _FILE_TOOLS
