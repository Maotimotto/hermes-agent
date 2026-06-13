"""Hermes V1.0.0 控制平面自定义错误类。

层级结构：
  HermesError（基类）
  ├── SessionNotFoundError
  ├── TurnNotFoundError
  ├── ApprovalDeniedError
  ├── ApprovalTimeoutError
  ├── RuntimeError (与 builtin 不冲突，完全限定名不同)
  └── WorkspaceError
      ├── WorkspaceLockedError
      └── WorkspaceNotFoundError
"""

from __future__ import annotations


class HermesError(Exception):
    """Hermes 控制平面错误基类。"""

    def __init__(self, message: str = "", *, code: str | None = None) -> None:
        super().__init__(message)
        self.code = code


class SessionNotFoundError(HermesError):
    """找不到指定 session。"""

    def __init__(self, session_id: str) -> None:
        super().__init__(f"Session not found: {session_id}")
        self.session_id = session_id


class TurnNotFoundError(HermesError):
    """找不到指定 turn。"""

    def __init__(self, turn_id: str) -> None:
        super().__init__(f"Turn not found: {turn_id}")
        self.turn_id = turn_id


class ApprovalDeniedError(HermesError):
    """审批被拒绝。"""

    def __init__(self, approval_id: str, reason: str | None = None) -> None:
        msg = f"Approval denied: {approval_id}"
        if reason:
            msg += f" — {reason}"
        super().__init__(msg)
        self.approval_id = approval_id
        self.reason = reason


class ApprovalTimeoutError(HermesError):
    """审批超时未响应。"""

    def __init__(self, approval_id: str, timeout_seconds: float = 0) -> None:
        super().__init__(
            f"Approval timed out after {timeout_seconds}s: {approval_id}",
        )
        self.approval_id = approval_id
        self.timeout_seconds = timeout_seconds


class HermesRuntimeError(HermesError):
    """运行时错误（provider 侧错误、内部状态异常等）。

    注意：名称故意不叫 RuntimeError 以避免遮蔽 Python builtin。
    """

    def __init__(
        self,
        message: str = "",
        *,
        code: str | None = None,
        provider: str | None = None,
    ) -> None:
        super().__init__(message, code=code)
        self.provider = provider


class WorkspaceError(HermesError):
    """工作区相关错误的基类。"""


class WorkspaceLockedError(WorkspaceError):
    """工作区已被其他 session 锁定。"""

    def __init__(self, workspace_id: str, locked_by: str) -> None:
        super().__init__(
            f"Workspace {workspace_id} is locked by session {locked_by}",
        )
        self.workspace_id = workspace_id
        self.locked_by = locked_by


class WorkspaceNotFoundError(WorkspaceError):
    """找不到指定工作区。"""

    def __init__(self, workspace_id: str) -> None:
        super().__init__(f"Workspace not found: {workspace_id}")
        self.workspace_id = workspace_id
