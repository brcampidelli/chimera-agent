from __future__ import annotations

import os
import sys

import pytest

from chimera.cli import main as cli_main


def _invoke_service_entrypoint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", ["chimera", "service", "run"])
    monkeypatch.setattr(type(cli_main.app), "__call__", lambda _app: None)
    monkeypatch.setattr("chimera.cli.service_cmd._wait_for_service_threads", lambda: None)
    cli_main._run_cli_entrypoint()


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
