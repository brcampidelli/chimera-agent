"""The drift alarms reach the two places a person reads the log: the screen and the report.

`chimera/decisions/drift.py` holds the arithmetic and its own tests. This holds the wiring: the alarm
is in `GET /api/decisions` and in `chimera decisions report`, and a log with nothing to say says nothing,
because an alarm row on every screen is a row nobody reads by the third day.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from chimera.config import Settings, get_settings
from chimera.decisions.governance import DECISION
from chimera.decisions.log import DecisionLog

BASE = {"decision": DECISION, "backend": "local_logprob", "model": "qwen3:4b", "prompt_hash": "h", "calibrated": True}


def _client(home: Path) -> TestClient:
    from chimera.api import build_api_app

    return TestClient(build_api_app(lambda: None, settings=Settings(CHIMERA_HOME=str(home))))  # type: ignore[arg-type, call-arg]


def _two_builds(home: Path) -> None:
    log = DecisionLog.for_home(home)
    for build in ("qwen3:4b@Q4_K_M", "qwen3:4b@Q5_K_M"):
        for p in (0.2, 0.3):
            log.answer({**BASE, "resolved_model": build, "p": p}, f"state {build} {p}", raw_p=p)


def test_a_log_with_nothing_to_report_has_no_alerts(tmp_path: Path) -> None:
    log = DecisionLog.for_home(tmp_path)
    log.answer({**BASE, "resolved_model": "qwen3:4b@Q4_K_M", "p": 0.2}, "ls", raw_p=0.2)

    assert _client(tmp_path).get("/api/decisions").json()["alerts"] == []


def test_the_screen_carries_a_build_change(tmp_path: Path) -> None:
    _two_builds(tmp_path)

    alerts = _client(tmp_path).get("/api/decisions").json()["alerts"]

    assert [a["kind"] for a in alerts] == ["model_changed"]
    assert alerts[0]["decision"] == DECISION and alerts[0]["model"] == "qwen3:4b"
    assert [b["build"] for b in alerts[0]["detail"]["builds"]] == ["qwen3:4b@Q4_K_M", "qwen3:4b@Q5_K_M"]


@pytest.fixture()
def cli_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path))
    get_settings.cache_clear()
    yield tmp_path
    get_settings.cache_clear()


def test_the_report_says_it_and_says_it_gates_nothing(cli_home: Path) -> None:
    from chimera.cli.main import app

    _two_builds(cli_home)

    out = CliRunner().invoke(app, ["decisions", "report"])

    assert out.exit_code == 0
    assert "drift alerts" in out.output and "model_changed" in out.output
    assert "nothing is gated by them" in out.output
    assert "qwen3:4b@Q4_K_M -> qwen3:4b@Q5_K_M" in out.output


def test_the_report_of_a_quiet_log_has_no_alert_section(cli_home: Path) -> None:
    from chimera.cli.main import app

    DecisionLog.for_home(cli_home).answer({**BASE, "resolved_model": "b", "p": 0.2}, "ls", raw_p=0.2)

    out = CliRunner().invoke(app, ["decisions", "report"])

    assert out.exit_code == 0 and "drift alerts" not in out.output
