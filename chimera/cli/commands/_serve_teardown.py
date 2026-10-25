"""Helpers for bounded and reliable server teardown."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from chimera.cli.service_cmd import AppMode


def _run_teardown_step(name: str, action: Any) -> None:
    """Run one teardown operation without letting it skip the remaining shutdown boundary."""
    from chimera.telemetry import get_logger

    try:
        action()
    except BaseException:
        get_logger("cli.serve").exception("service teardown step failed: %s", name)


def _teardown_app_server(
    *,
    publication: Any,
    desktop_bridge: Any,
    claim: Any,
    cron_stop: Any,
    messaging: Any,
    api: Any,
    mode: AppMode,
    home: Path,
) -> None:
    """Release owned resources independently; publication removal is the final boundary."""
    try:
        if publication is not None:
            _run_teardown_step("publication.begin_shutdown", publication.begin_shutdown)
        _run_teardown_step("desktop_bridge.close", desktop_bridge.close)
        _run_teardown_step("claim.release", claim.release)
        for resource_name in ("guest_server", "device_door", "browser_link"):
            resource = getattr(api.state, resource_name, None)
            close = getattr(resource, "stop", None) or getattr(resource, "shutdown", None)
            if close is not None:
                _run_teardown_step(f"api.{resource_name}.stop", close)
        if cron_stop is not None:
            _run_teardown_step("cron_stop.set", cron_stop.set)
        _run_teardown_step("messaging.stop_all", messaging.stop_all)
    finally:
        if mode == "service" and publication is not None:
            _run_teardown_step(
                "publication.finish_shutdown", lambda: publication.finish_shutdown(home)
            )


def _run_server_with_teardown(server: Any, sock: Any, *, mode: AppMode, teardown: Any) -> None:
    """Run Uvicorn and guarantee teardown after its return or failure."""
    try:
        server.run(sockets=[sock])
        from chimera.telemetry import get_logger

        _log_server_run_returned(server)
    except BaseException:
        from chimera.telemetry import get_logger

        get_logger("cli.serve").exception("server.run raised; entering teardown")
        raise
    finally:
        teardown()
        if mode == "service":
            from chimera.telemetry import get_logger

            get_logger("cli.serve").warning("service teardown complete")


def _log_server_run_returned(server: Any) -> None:
    """Record Uvicorn's final state after a normal run return."""
    from chimera.telemetry import get_logger

    captured_signals = getattr(server, "_captured_signals", ())
    get_logger("cli.serve").warning(
        "server.run returned: should_exit=%s force_exit=%s signals=%s",
        server.should_exit, getattr(server, "force_exit", False), list(captured_signals),
    )
