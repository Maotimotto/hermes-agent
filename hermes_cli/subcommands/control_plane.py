"""``hermes control-plane`` subcommand parser.

Thin wrapper around ``scripts/run_control_plane.py`` so users can launch the
V1.0.0 Control Plane daemon via the canonical ``hermes`` entry point instead of
``python -m scripts.run_control_plane``.

Usage::

    hermes control-plane start                  # default port 18765
    hermes control-plane start --port 28765
    hermes control-plane start --reload         # dev hot-reload
    hermes control-plane start --db-path /tmp/cp.db

Design notes
~~~~~~~~~~~~
- The actual server boot logic lives in ``scripts/run_control_plane.py`` and is
  re-used verbatim. We forward CLI args by mutating ``sys.argv`` so we don't
  have to keep two argument tables in sync.
- ``start`` is the only action today. ``stop`` / ``status`` are intentionally
  not provided yet — the daemon currently has no PID file, and the existing
  ``hermes dashboard --stop`` pattern relies on a process-table scan that we
  don't want to replicate until a real lifecycle story exists.
"""

from __future__ import annotations

import argparse
import sys
from typing import Callable


def build_control_plane_parser(subparsers, *, cmd_control_plane: Callable) -> None:
    """Attach the ``control-plane`` subcommand."""
    cp_parser = subparsers.add_parser(
        "control-plane",
        help="Manage the Hermes Control Plane daemon (V1.0.0 codex+claude runtime)",
        description=(
            "Hermes Control Plane is the V1.0.0 standalone daemon that hosts "
            "the Codex App Server + Claude Agent SDK runtimes, session/event/"
            "approval store, and HTTP/WebSocket API consumed by the web "
            "console at /control-plane."
        ),
    )
    cp_actions = cp_parser.add_subparsers(
        dest="control_plane_action",
        metavar="ACTION",
        help="Control Plane action",
    )

    # ── start ────────────────────────────────────────────────────────────
    start_parser = cp_actions.add_parser(
        "start",
        help="Start the control-plane daemon (foreground)",
        description="Launch the V1.0.0 Control Plane daemon in the foreground.",
    )
    start_parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Bind host (default: 127.0.0.1; set 0.0.0.0 to expose on LAN)",
    )
    start_parser.add_argument(
        "--port",
        type=int,
        default=18765,
        help="Bind port (default: 18765)",
    )
    start_parser.add_argument(
        "--db-path",
        default=None,
        help="Override SessionStore SQLite path (default: ~/.hermes/control_plane.db)",
    )
    start_parser.add_argument(
        "--reload",
        action="store_true",
        help="Hot-reload on code changes (dev mode; uses uvicorn factory)",
    )
    start_parser.add_argument(
        "--log-level",
        default="info",
        choices=["debug", "info", "warning", "error", "critical"],
        help="uvicorn log level (default: info)",
    )

    cp_parser.set_defaults(func=cmd_control_plane)


def cmd_control_plane(args: argparse.Namespace) -> int:
    """Dispatch ``hermes control-plane <action>``.

    For now, ``start`` is the only action and forwards to
    ``scripts.run_control_plane.main()`` by rebuilding its argv.
    """
    action = getattr(args, "control_plane_action", None)
    if action is None:
        # No action: print help and exit non-zero so scripts can detect it.
        print(
            "error: missing action. Try `hermes control-plane start --help`.",
            file=sys.stderr,
        )
        return 2

    if action == "start":
        return _dispatch_start(args)

    print(f"error: unknown action {action!r}", file=sys.stderr)
    return 2


def _dispatch_start(args: argparse.Namespace) -> int:
    """Forward ``start`` to ``scripts.run_control_plane.main()``."""
    forwarded: list[str] = ["scripts.run_control_plane"]
    forwarded += ["--host", str(args.host)]
    forwarded += ["--port", str(args.port)]
    forwarded += ["--log-level", str(args.log_level)]
    if getattr(args, "db_path", None):
        forwarded += ["--db-path", str(args.db_path)]
    if getattr(args, "reload", False):
        forwarded += ["--reload"]

    saved = sys.argv
    sys.argv = forwarded
    try:
        try:
            from scripts.run_control_plane import main as _start_main
        except ImportError as exc:  # noqa: BLE001
            print(
                "error: failed to import scripts.run_control_plane: "
                f"{exc}\n"
                "Hint: install the control-plane extras with "
                "`pip install -e '.[control-plane]'` from the hermes-agent repo "
                "root, or run from a checkout where scripts/ is on sys.path.",
                file=sys.stderr,
            )
            return 1
        return int(_start_main() or 0)
    finally:
        sys.argv = saved
