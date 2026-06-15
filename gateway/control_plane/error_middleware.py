"""统一错误中间件 — 把所有未捕获的异常 / HTTPException / RequestValidationError
包成一致的 ErrorResponse JSON 信封。

挂在 FastAPI 子 app 上即可，通过 ``install_error_handlers(app)`` 注册。

为什么需要这个？
- FastAPI 默认的 422 JSON 结构跟我们的 ErrorResponse 不同，前端要统一处理
- 未捕获的 500 不带 ``error.code``，Debug 时缺少 request_id
- provider 健康检查等场景有内部异常需要优雅兜底
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from gateway.control_plane.schemas import ErrorBody, ErrorResponse

logger = logging.getLogger(__name__)


def _make_request_id() -> str:
    return uuid.uuid4().hex[:12]


def _build_envelope(
    status_code: int,
    code: str,
    message: str,
    *,
    request_id: str | None = None,
    details: Any | None = None,
) -> JSONResponse:
    body = ErrorResponse(
        error=ErrorBody(
            code=code,
            message=message,
            request_id=request_id,
            details=details,
        )
    )
    return JSONResponse(
        status_code=status_code,
        content=body.model_dump(exclude_none=True),
    )


async def _handle_http_exception(
    request: Request, exc: StarletteHTTPException
) -> JSONResponse:
    """HTTPException（含 FastAPI 的）→ ErrorResponse 信封。"""
    rid = _make_request_id()
    code = "not_found" if exc.status_code == 404 else f"http_{exc.status_code}"
    return _build_envelope(
        exc.status_code,
        code,
        str(exc.detail) if exc.detail else f"HTTP {exc.status_code}",
        request_id=rid,
    )


async def _handle_validation_error(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """RequestValidationError（422）→ ErrorResponse 信封 + 附带验证详情。"""
    rid = _make_request_id()
    return _build_envelope(
        422,
        "validation_error",
        "request validation failed",
        request_id=rid,
        details=exc.errors(),
    )


async def _handle_unhandled(request: Request, exc: Exception) -> JSONResponse:
    """兜底：所有未捕获异常 → 500 + ErrorResponse 信封。

    日志记录完整 traceback，响应只给外部友好信息 + request_id。
    """
    rid = _make_request_id()
    logger.exception(
        "[error_middleware] unhandled exception request_id=%s path=%s",
        rid,
        request.url.path,
    )
    return _build_envelope(
        500,
        "internal_error",
        "An internal error occurred. If this persists, contact support.",
        request_id=rid,
    )


def install_error_handlers(app: FastAPI) -> None:
    """注册统一错误处理器到 FastAPI 应用。

    调用时机：在 ``create_control_plane_app()`` 里、router 注册之后。
    """
    app.add_exception_handler(StarletteHTTPException, _handle_http_exception)
    app.add_exception_handler(RequestValidationError, _handle_validation_error)
    app.add_exception_handler(Exception, _handle_unhandled)
