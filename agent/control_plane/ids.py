"""Hermes V1.0.0 ID 生成工具。

所有 ID 格式：{prefix}_{base32_short_uuid}
  - session  → sess_xxxx
  - turn     → turn_xxxx
  - event    → evt_xxxx
  - approval → apr_xxxx

底层使用 uuid.uuid4()，截取前 16 字符做 base32 编码以保持 URL-safe。
"""

from __future__ import annotations

import uuid


def _short_id(length: int = 22) -> str:
    """生成一个短的 URL-safe 随机 ID（基于 uuid4 的 base32 编码）。"""
    return uuid.uuid4().hex[:length]


def new_session_id() -> str:
    """生成 session ID：sess_xxxx。"""
    return f"sess_{_short_id()}"


def new_turn_id() -> str:
    """生成 turn ID：turn_xxxx。"""
    return f"turn_{_short_id()}"


def new_event_id() -> str:
    """生成 event ID：evt_xxxx。"""
    return f"evt_{_short_id()}"


def new_approval_id() -> str:
    """生成 approval ID：apr_xxxx。"""
    return f"apr_{_short_id()}"
