from __future__ import annotations

import os
import sys

import pytest

from chimera.cli import main as cli_main

_EXIT_CODES: list[int] = []


def _own_thread_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS"):
        if name in os.environ:
            monkeypatch.setenv(name, os.environ[name])
        else:
            monkeypatch.setenv(name, "")
            monkeypatch.delenv(name)


def _invoke_service_entrypoint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", ["chimera", "service", "run"])
    monkeypatch.setattr(type(cli_main.app), "__call__", lambda _app: None)
    monkeypatch.setattr("chimera.cli.service_cmd._wait_for_service_threads", lambda: None)
    monkeypatch.setattr(os, "_exit", lambda code: _EXIT_CODES.append(code))
    monkeypatch.setattr("chimera.cli.service_cmd.logging.shutdown", lambda: None)
    _own_thread_env(monkeypatch)
    cli_main._run_cli_entrypoint()


def test_service_entrypoints_exit_with_application_status(monkeypatch: pytest.MonkeyPatch) -> None:
    import importlib.util
    from pathlib import Path

    backend_path = (
        Path(__file__).resolve().parent.parent
        / "apps"
        / "desktop"
        / "src-tauri"
        / "sidecar"
        / "chimera_backend.py"
    )
    spec = importlib.util.spec_from_file_location("chimera_backend_exit_test", backend_path)
    assert spec is not None and spec.loader is not None
    backend = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(backend)

    for entrypoint in (cli_main._run_cli_entrypoint, backend.main):
        for outcome, expected in (("return", 0), ("exception", 1), ("system_exit", 3)):
            _EXIT_CODES.clear()
            monkeypatch.setattr(sys, "argv", ["chimera", "service", "run"])
            monkeypatch.setattr("chimera.cli.service_cmd._wait_for_service_threads", lambda: None)
            monkeypatch.setattr("chimera.cli.service_cmd.logging.shutdown", lambda: None)
            monkeypatch.setattr(os, "_exit", lambda code: _EXIT_CODES.append(code))
            _own_thread_env(monkeypatch)

            def invoke(_app: object, result: str = outcome) -> None:
                if result == "exception":
                    raise RuntimeError("expected test failure")
                if result == "system_exit":
                    raise SystemExit(3)

            monkeypatch.setattr(type(cli_main.app), "__call__", invoke)
            entrypoint()
            assert [expected] == _EXIT_CODES, (entrypoint, outcome, _EXIT_CODES)


def test_service_entrypoint_sets_single_thread_blas_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS"):
        monkeypatch.setenv(name, "temporarily-set")
        monkeypatch.delenv(name)

    _invoke_service_entrypoint(monkeypatch)

    assert os.environ["OPENBLAS_NUM_THREADS"] == "1"
    assert os.environ["OMP_NUM_THREADS"] == "1"


def test_service_entrypoint_preserves_existing_thread_counts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENBLAS_NUM_THREADS", "4")
    monkeypatch.setenv("OMP_NUM_THREADS", "3")

    _invoke_service_entrypoint(monkeypatch)

    assert os.environ["OPENBLAS_NUM_THREADS"] == "4"
    assert os.environ["OMP_NUM_THREADS"] == "3"
