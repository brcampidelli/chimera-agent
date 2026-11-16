"""`chimera models` and `GET /api/routing` answer for EVERY role of the routing table.

Before this, `chimera models` showed the ladder and the fusion cast and nothing else: the roles the
product runs without ever naming a model for (chat, the code roles, the cron, the subagents) were
absent, so "which model does the reviewer use?" had no place to be asked. The screens now read the
table, which has a row per role, with where each model came from and whether the gateway would put the
effort on the wire.

The policy case is the table's one deliberate raise (``ProviderDeniedError``): a screen that turned it
into a 500 would read as a fault in the product when it is the owner's own policy.
"""

from __future__ import annotations

import io
import json
import re
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from rich.console import Console

from chimera.api import build_api_app, screen_credential
from chimera.api.config_api import read_config, routing_rows
from chimera.cli.commands import setup as setup_commands
from chimera.config import Settings, get_settings
from chimera.providers.routing import ROLE_IDS


def _settings(**env: str) -> Settings:
    return Settings(_env_file=None, **env)  # type: ignore[call-arg,arg-type]


def _rendered(monkeypatch: pytest.MonkeyPatch, settings: Settings) -> str:
    out = io.StringIO()
    monkeypatch.setattr(setup_commands, "console", Console(file=out, width=400, color_system=None))
    monkeypatch.setattr(setup_commands, "get_settings", lambda: settings)
    setup_commands._render_models()
    return re.sub(r"[ \t]+", " ", out.getvalue())


def test_chimera_models_lists_every_role_with_its_origin(monkeypatch: pytest.MonkeyPatch) -> None:
    text = _rendered(
        monkeypatch,
        _settings(CHIMERA_DEFAULT_MODEL="openrouter/test/dflt#high", CHIMERA_WEAK_MODEL="ollama_chat/small"),
    )
    labels = setup_commands._ROLE_LABELS
    for role in ROLE_IDS:
        label = labels.get(role, role)
        assert re.search(rf"│ {re.escape(label)} ", text), f"{role} is missing from `chimera models`"
    weak = next(line for line in text.splitlines() if line.startswith("│ tier: weak "))
    assert "ollama_chat/small" in weak and "config" in weak and "weak_model" in weak
    default = next(line for line in text.splitlines() if line.startswith("│ default (Tier 1) "))
    assert "openrouter/test/dflt" in default and "high" in default
    # A role that follows another says so, and which one.
    assert "follows default" in next(line for line in text.splitlines() if line.startswith("│ code.edit "))


def test_chimera_models_says_when_the_effort_is_not_sent(monkeypatch: pytest.MonkeyPatch) -> None:
    sent = _rendered(monkeypatch, _settings(CHIMERA_DEFAULT_MODEL="openrouter/test/dflt#high"))
    dropped = _rendered(monkeypatch, _settings(CHIMERA_DEFAULT_MODEL="ollama_chat/local#high"))
    assert "effort not sent on this route" not in next(
        line for line in sent.splitlines() if line.startswith("│ default (Tier 1) ")
    )
    assert "effort not sent on this route" in next(
        line for line in dropped.splitlines() if line.startswith("│ default (Tier 1) ")
    )


def test_the_api_rows_are_the_table_with_the_effort_verdict() -> None:
    body = routing_rows(_settings(CHIMERA_DEFAULT_MODEL="ollama_chat/local#high"))
    assert [row["role"] for row in body["rows"]] == list(ROLE_IDS)
    default = next(row for row in body["rows"] if row["role"] == "default")
    assert (default["model"], default["effort"], default["effort_sent"]) == ("ollama_chat/local", "high", False)
    assert body["denied"] == ""
    escalation = next(row for row in body["rows"] if row["role"] == "escalation")
    assert escalation["kind"] == "list" and len(escalation["members"]) == 3


PRIVATE = "/home/bruno/clientes/confidencial/voz.onnx"


def test_a_local_path_is_a_file_name_in_the_api_and_in_chimera_models(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(
        CHIMERA_CHAT_VOICE_MODEL=PRIVATE,
        CHIMERA_IMAGE_MODEL_LOCAL=r"C:\clientes\confidencial\sd",
        CHIMERA_COMPLETE_MODEL="~/clientes/confidencial/coder.gguf",
    )
    body = routing_rows(settings)
    dumped = json.dumps(body)
    for private in ("confidencial", "clientes", "/home/bruno"):
        assert private not in dumped
    voice = next(row for row in body["rows"] if row["role"] == "chat_voice")
    assert (voice["model"], voice["local_file"]) == ("voz.onnx", True)
    text = _rendered(monkeypatch, settings)
    assert "confidencial" not in text and "voz.onnx" in text and "local file" in text


def test_the_screen_values_carry_no_credential() -> None:
    from chimera.providers.routing import public_model

    assert public_model("https://user:secret@host.example:8443/v1?token=abc") == (
        "https://host.example:8443/v1", False,
    )
    assert public_model("openrouter/test/model") == ("openrouter/test/model", False)
    assert public_model("sk-or-v1-" + "a" * 40)[0] != "sk-or-v1-" + "a" * 40
    body = routing_rows(_settings(CHIMERA_DEFAULT_MODEL="https://u:pw@h.example/v1"))
    assert "pw" not in json.dumps(body)


SECRETS = ("hunter2", "tok-9f8e7d", "svc-user", "confidencial", "/home/bruno")


def _nothing_private(text: str) -> None:
    for secret in SECRETS:
        assert secret not in text, f"{secret!r} reached the public view"


def test_a_denied_model_with_a_credential_url_is_not_published(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Codex, 2nd review: `denied` carried the ProviderDeniedError message, which names the WHOLE
    # model, URL userinfo and token included.
    monkeypatch.setenv(
        "CHIMERA_DEFAULT_MODEL", "https://svc-user:hunter2@llm.confidencial.example/v1?token=tok-9f8e7d"
    )
    _deny_everything(tmp_path, monkeypatch)
    settings = get_settings()
    body = routing_rows(settings)
    assert body["denied"] and body["rows"] == []
    _nothing_private(json.dumps(body))
    typer_exit = False
    import typer

    try:
        text = _rendered(monkeypatch, settings)
    except typer.Exit:
        typer_exit = True
        text = ""
    assert typer_exit and text == ""
    out = io.StringIO()
    monkeypatch.setattr(setup_commands, "console", Console(file=out, width=400, color_system=None))
    with pytest.raises(typer.Exit):
        setup_commands._render_models()
    _nothing_private(out.getvalue())
    assert "denied" in out.getvalue()


def test_a_decision_backend_that_is_a_private_path_is_not_published(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Codex, 2nd review: `via` of the decisions role repeats the backend in "unknown decision backend".
    settings = _settings(CHIMERA_DECISION_BACKEND="/home/bruno/clientes/confidencial/backend")
    body = routing_rows(settings)
    decisions = next(row for row in body["rows"] if row["role"] == "decisions")
    assert "unknown decision backend" in decisions["via"] and "backend" in decisions["via"]
    _nothing_private(json.dumps(body))
    _nothing_private(_rendered(monkeypatch, settings))


def test_every_string_of_the_public_answer_goes_through_one_sanitiser() -> None:
    from chimera.providers.routing import _publish

    leaked = {"a": ["see /home/bruno/x/y.txt", {"model": "/home/bruno/m.onnx", "new_field": "https://u:hunter2@h/p?token=tok-9f8e7d"}]}
    _nothing_private(json.dumps(_publish(leaked)))


def _deny_everything(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    policy = tmp_path / "deny_all.json"
    policy.write_text(
        json.dumps({"rules": [{"action": "provider.use", "resource": "*", "effect": "deny"}]}),
        encoding="utf-8",
    )
    monkeypatch.setenv("CHIMERA_POLICY_FILE", str(policy))
    get_settings.cache_clear()


def test_a_policy_that_denies_every_provider_is_an_answer_not_a_500(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _deny_everything(tmp_path, monkeypatch)
    screen = screen_credential.for_mode("app", server_token="owner-token-for-the-routing-probe")
    app = build_api_app(lambda: None, settings=get_settings(), screen=screen)  # type: ignore[arg-type,return-value]
    client = TestClient(app)
    client.headers[screen_credential.HEADER_NAME] = str(screen.value)
    response = client.get("/api/routing")
    assert response.status_code == 200
    body: dict[str, Any] = response.json()
    assert body["rows"] == [] and "deployment policy" in body["denied"]


def test_chimera_models_refuses_cleanly_when_the_policy_denies_everything(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import typer

    _deny_everything(tmp_path, monkeypatch)
    with pytest.raises(typer.Exit):
        _rendered(monkeypatch, get_settings())


def test_the_config_models_block_still_shows_what_the_owner_typed() -> None:
    # The rows of the Settings screen edit these values: a preset's rung is NOT something they typed.
    untouched = read_config(_settings())["models"]
    assert untouched["weak"] == untouched["mid"] == untouched["orchestrator"] == ""
    assert untouched["voice_model"] == untouched["voice_work_model"] == ""
    typed = read_config(
        _settings(CHIMERA_MID_MODEL="openrouter/test/mid#low", CHIMERA_VOICE_MODEL="openrouter/test/voice")
    )["models"]
    assert typed["mid"] == "openrouter/test/mid#low"
    assert typed["voice_model"] == "openrouter/test/voice"
    assert typed["weak"] == "" and typed["tiers"]["mid"] == "openrouter/test/mid#low"

def test_a_bracket_inside_a_query_does_not_leave_the_rest_of_the_token_behind() -> None:
    """The URL match stopped at `)`, so `?token=abc)def456` published `)def456` (third Codex review)."""
    from chimera.providers.routing import public_text

    shown = public_text("denied https://u:pw@host.example/v1?token=abc)def456 for this role")

    assert "def456" not in shown and "abc" not in shown and "pw" not in shown
    assert "https://host.example/v1" in shown


@pytest.mark.parametrize(
    "path",
    ["/home/bruno/Cliente Confidencial/backend", "C:\\Users\\me\\Client X\\m.gguf"],
)
def test_a_quoted_path_with_spaces_is_only_its_file_name(path: str) -> None:
    """`repr()` puts a setting between quotes in an error, and the path inside may hold spaces:
    cutting at the first one published `Cliente Confidencial/backend`."""
    from chimera.providers.routing import public_text

    shown = public_text(f"unknown decision backend {path!r}; one of a, b")

    assert "Cliente" not in shown and "Client X" not in shown and "bruno" not in shown and "me" + chr(92) not in shown
