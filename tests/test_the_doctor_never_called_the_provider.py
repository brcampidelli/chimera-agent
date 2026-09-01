"""`Ready` was an assertion about the name of an environment variable.

`doctor` printed "Ready — at least one provider key is configured" from `configured_providers()`,
which reads names. The code already knows this is permissive: the comment a few lines above says a
typo'd `GROK_API_KEY` is indistinguishable from a provider.

So a revoked key, an account with no credit, or a value pasted with a trailing space all pass the
command whose name is *doctor*, and fail on the first real call — which on this deployment is a cron
at three in the morning. The argument for measuring instead of asserting is already written in this
project, in `config_api.pricing_capability`: the time to find out is while reading the doctor.

Off by default, because `doctor` should stay instant, offline and free — the reason it is worth
running at all. `--probe` is the version that costs one token and answers the question.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from chimera.cli.main import app
from chimera.config import get_settings


@pytest.fixture(autouse=True)
def _home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path))
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test-que-nao-vale-nada")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _gateway_que(monkeypatch: pytest.MonkeyPatch, resposta: Any) -> list[str]:
    """Replace the gateway's one-shot call, and record whether it was reached."""
    chamadas: list[str] = []

    def quick(self: Any, prompt: str, **_kwargs: Any) -> str:
        chamadas.append(prompt)
        if isinstance(resposta, Exception):
            raise resposta
        return str(resposta)

    from chimera.providers.gateway import LLMGateway

    monkeypatch.setattr(LLMGateway, "quick", quick)
    return chamadas


def test_without_probe_it_calls_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    """The default has to stay free and offline, or it stops being the command you run first."""
    chamadas = _gateway_que(monkeypatch, "ok")

    saida = CliRunner().invoke(app, ["doctor"])

    assert chamadas == []
    assert "Ready" in saida.stdout


def test_without_probe_it_says_what_it_checked(monkeypatch: pytest.MonkeyPatch) -> None:
    """"Ready" alone is the claim this whole change is about. It has to say it read a name."""
    _gateway_que(monkeypatch, "ok")

    saida = CliRunner().invoke(app, ["doctor"])

    assert "--probe" in saida.stdout


def test_with_probe_a_live_answer_reads_as_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    chamadas = _gateway_que(monkeypatch, "ok")

    saida = CliRunner().invoke(app, ["doctor", "--probe"])

    assert len(chamadas) == 1
    assert "Ready" in saida.stdout


def test_with_probe_a_refused_key_reads_as_not_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    """The whole point: this is the case the old line called Ready."""
    _gateway_que(monkeypatch, RuntimeError("401 - invalid api key"))

    saida = CliRunner().invoke(app, ["doctor", "--probe"])

    assert "Not ready" in saida.stdout
    assert "401" in saida.stdout


def test_the_probe_failure_is_redacted(monkeypatch: pytest.MonkeyPatch) -> None:
    """A provider's error body carries an echoed prompt and its own routing trace, and sometimes the
    credential itself. Printing it raw would make the diagnostic the leak.

    Asserted with a key SHAPE, which is what `redact` catches on this branch. A secret given away by
    its PLACE — a query parameter, a header — is a separate change on another branch, and a test
    that leaned on it would be asserting that branch's behaviour from this one.
    """
    _gateway_que(monkeypatch, RuntimeError("401 rejected key sk-proj-AAAAAAAAAAAAAAAAAAAAAA"))

    saida = CliRunner().invoke(app, ["doctor", "--probe"])

    assert "sk-proj-AAAAAAAAAAAAAAAAAAAAAA" not in saida.stdout
    assert "401" in saida.stdout


def test_a_probe_failure_does_not_crash_the_command(monkeypatch: pytest.MonkeyPatch) -> None:
    """`doctor` reports; it does not fail. Someone running it because something is already broken
    must still see the rest of the table."""
    _gateway_que(monkeypatch, RuntimeError("qualquer coisa"))

    saida = CliRunner().invoke(app, ["doctor", "--probe"])

    assert saida.exit_code == 0
    assert "Chimera version" in saida.stdout
