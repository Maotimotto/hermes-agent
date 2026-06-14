"""Hermes Control Plane — 独立 daemon 启动入口。

用法：
    python -m scripts.run_control_plane                # 端口 18765
    python -m scripts.run_control_plane --port 28765   # 自定义端口
    python -m scripts.run_control_plane --reload       # dev 模式热重载

设计目标：
  - 只挂载 control-plane sub-app，不挂载 hermes 总网关的其他模块
    （平台 adapter / kanban / 终端 PTY 等）
  - 端口可指定，避免和日常运行的 `hermes gateway run` 进程冲突
  - 自动读 `~/.hermes/config.yaml` → runtime_configs，无需手工指定 claude key
  - 自动加载 `~/.hermes-codex-env.sh` 风格的 OPENAI_API_KEY env，
    供 codex 子进程使用（codex 自己读 ~/.codex/config.toml）
  - 任何启动失败都打印清晰的错误，绝不 silent

启动后能调的 API（以 :18765 为例）：
  GET  http://127.0.0.1:18765/control-plane/health
  POST http://127.0.0.1:18765/control-plane/sessions   { runtime_kind, model, ... }
  POST http://127.0.0.1:18765/control-plane/sessions/{id}/turns  { prompt }
  GET  http://127.0.0.1:18765/control-plane/sessions/{id}/events
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path


def _ensure_env() -> None:
    """确保 OPENAI_API_KEY / OPENAI_BASE_URL 已加载到当前进程。

    优先级：
      1. 已存在的 env（来自父 shell source）→ 不动
      2. ~/.hermes-codex-env.sh 文件存在 → 解析 export 语句
      3. 仍缺失 → 警告但不阻断（codex runtime 跑 codex 子进程时才会报错）
    """
    if os.environ.get("OPENAI_API_KEY"):
        return
    env_file = Path.home() / ".hermes-codex-env.sh"
    if not env_file.exists():
        logging.warning(
            "[launcher] OPENAI_API_KEY 未设置且 %s 不存在 —— codex 会话将失败",
            env_file,
        )
        return
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line.startswith("export "):
            continue
        # 去掉 "export "
        kv = line[7:]
        if "=" not in kv:
            continue
        k, _, v = kv.partition("=")
        v = v.strip().strip('"').strip("'")
        if k and v and k not in os.environ:
            os.environ[k] = v
            logging.info("[launcher] loaded %s from %s", k, env_file.name)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Hermes Control Plane standalone daemon launcher",
    )
    parser.add_argument(
        "--host", default="127.0.0.1",
        help="bind address (default 127.0.0.1, 仅本机访问；改 0.0.0.0 暴露到局域网)",
    )
    parser.add_argument(
        "--port", type=int, default=18765,
        help="HTTP 端口 (default 18765, 避开常用端口与日常 hermes 网关)",
    )
    parser.add_argument(
        "--db-path", default=None,
        help="SQLite DB 路径 (default ~/.hermes/control_plane.db)",
    )
    parser.add_argument(
        "--log-level", default="info",
        choices=["debug", "info", "warning", "error"],
    )
    parser.add_argument(
        "--reload", action="store_true",
        help="开发模式热重载（监听代码变化重启 worker）",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper()),
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )
    log = logging.getLogger("cp-launcher")

    # 1. 加载 OPENAI_API_KEY 等 env
    _ensure_env()

    # 2. 状态报告
    has_openai = bool(os.environ.get("OPENAI_API_KEY"))
    log.info("─── Hermes Control Plane Launcher ───")
    log.info("  bind      : %s:%d", args.host, args.port)
    log.info("  db_path   : %s", args.db_path or "(default ~/.hermes/control_plane.db)")
    log.info("  OPENAI env: %s", "✓ present" if has_openai else "✗ missing")
    log.info("  CODEX_MODEL: %s", os.environ.get("CODEX_MODEL", "(unset)"))
    log.info("  base_url  : %s", os.environ.get("OPENAI_BASE_URL", "(unset)"))
    log.info("──────────────────────────────────────")

    # 3. 构造 FastAPI app + 挂载 control-plane
    try:
        from fastapi import FastAPI
        from gateway.control_plane import mount_to
        from gateway.control_plane.app import init_store
    except Exception as exc:  # noqa: BLE001
        log.error("import 失败: %s", exc)
        log.error("提示: 必须从 hermes-agent 仓库根目录跑，或 PYTHONPATH 指向仓库根。")
        return 2

    app = FastAPI(
        title="Hermes Control Plane (standalone)",
        version="1.0.0-standalone",
        description="独立 daemon — 仅挂载 /control-plane sub-app",
    )

    # 根路径加个 hint，方便手动 curl 验证
    @app.get("/")
    async def _root() -> dict[str, str]:
        return {
            "service": "hermes-control-plane",
            "mode": "standalone",
            "health": "/control-plane/health",
            "sessions": "/control-plane/sessions",
        }

    try:
        mount_to(app, db_path=args.db_path)
    except Exception:
        log.exception("mount_to 失败")
        return 3

    # FastAPI sub-app 的 startup 事件不会被 parent app 自动触发，
    # 必须在 parent 的 startup 里手动调 init_store(sub_app)。
    @app.on_event("startup")
    async def _trigger_sub_startup() -> None:
        # mount_to 把 sub-app 挂到 prefix="/control-plane"，找回来
        for mount_route in app.routes:
            # Starlette Mount 类型
            if getattr(mount_route, "path", None) == "/control-plane":
                sub_app = mount_route.app  # type: ignore[attr-defined]
                log.info("触发 sub-app startup: SessionStore + RuntimeRegistry 初始化…")
                await init_store(sub_app)
                log.info("✓ control-plane 子应用初始化完成")
                return
        log.error("未找到挂载在 /control-plane 的 sub-app，初始化失败")

    # 4. 起 uvicorn
    try:
        import uvicorn
    except ImportError:
        log.error("缺 uvicorn，跑 `uv pip install uvicorn` 或 `pip install uvicorn`")
        return 4

    log.info("启动中…  Ctrl+C 退出")
    if args.reload:
        # reload 模式必须传 import string
        uvicorn.run(
            "scripts.run_control_plane:_make_app_for_reload",
            host=args.host,
            port=args.port,
            log_level=args.log_level,
            reload=True,
            factory=True,
        )
    else:
        uvicorn.run(app, host=args.host, port=args.port, log_level=args.log_level)
    return 0


def _make_app_for_reload():
    """供 --reload 模式用的 app factory。"""
    _ensure_env()
    from fastapi import FastAPI
    from gateway.control_plane import mount_to

    app = FastAPI(title="Hermes Control Plane (standalone, reload)")
    mount_to(app)
    return app


if __name__ == "__main__":
    sys.exit(main())
