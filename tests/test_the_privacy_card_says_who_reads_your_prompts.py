"""The Security screen's privacy card says who receives a prompt, and what it may keep (study 29, P5.6).

The answer was spread over a dozen model settings — tier ladder, fusion cast, voice models,
fallbacks, the embedder behind semantic memory — and the one surface that reaches OpenRouter outside
the gateway (the Decisions API backend) was invisible. `GET /api/config` now carries a `privacy`
block built from configuration in one place (`chimera/providers/privacy.py`), and these tests hold
what it reports.
"""

from __future__ import annotations

import pytest

from chimera.api.config_api import read_config
from chimera.api.schemas import ConfigOut
from chimera.config import Settings
from chimera.providers.privacy import privacy_snapshot, prompt_routes


def _settings(**env: str) -> Settings:
    return Settings(**env)  # type: ignore[arg-type]


def test_the_block_is_in_the_config_read_and_fits_its_schema() -> None:
    snapshot = read_config(_settings())
    ConfigOut.model_validate(snapshot)
    privacy = snapshot["privacy"]
    assert privacy["openrouter_data_collection"] == "allow"
    assert privacy["openrouter_zdr"] is False
    assert privacy["unscoped"] == []


def test_the_routes_name_each_provider_once_with_every_role_that_sends_to_it() -> None:
    routes = prompt_routes(
        _settings(
            CHIMERA_DEFAULT_MODEL="openrouter/deepseek/deepseek-chat",
            CHIMERA_WEAK_MODEL="ollama_chat/llama3",
            CHIMERA_FALLBACK_MODELS="openai/gpt-4.1-mini",
            CHIMERA_FUSION_PANEL="openrouter/a/b",
            CHIMERA_FUSION_JUDGE="openrouter/c/d",
            CHIMERA_FUSION_SYNTHESIZER="openrouter/c/d",
        )
    )
    by_name = {route["provider"]: route for route in routes}
    assert routes[0]["provider"] == "openrouter", "the default model's provider comes first"
    assert {"default", "fusion_panel", "fusion_judge", "fusion_synthesizer"} <= set(
        by_name["openrouter"]["roles"]
    )
    assert by_name["openrouter"]["local"] is False
    assert by_name["ollama_chat"]["local"] is True
    assert "weak" in by_name["ollama_chat"]["roles"]
    assert by_name["openai"]["roles"] == ["fallback"]
    assert len(routes) == len(by_name), "a provider is listed once"


def test_the_embedder_counts_only_while_semantic_memory_is_on() -> None:
    """With semantic memory off the embedder sends nothing; with it on, stored memory leaves the
    machine for it, which is exactly what an owner reading this card needs to see."""
    off = _settings(CHIMERA_EMBED_MODEL="gemini/text-embedding-004")
    on = _settings(CHIMERA_EMBED_MODEL="gemini/text-embedding-004", CHIMERA_SEMANTIC_MEMORY="true")
    assert all("embeddings" not in route["roles"] for route in prompt_routes(off))
    gemini = {route["provider"]: route for route in prompt_routes(on)}["gemini"]
    assert gemini["roles"] == ["embeddings"]


def test_the_decisions_backend_is_named_as_reaching_openrouter_without_the_preference() -> None:
    """`chimera/decisions/openrouter.py` posts to a separate endpoint and does not send the routing
    preference. With `deny` set, the card must not let that read as covering every call."""
    settings = _settings(
        CHIMERA_DECISION_BACKEND="openrouter_decisions", CHIMERA_OPENROUTER_DATA_COLLECTION="deny"
    )
    snapshot = privacy_snapshot(settings)
    assert snapshot["unscoped"] == ["decisions"]
    assert "decisions" in {r["provider"]: r for r in snapshot["routes"]}["openrouter"]["roles"]
    assert privacy_snapshot(_settings())["unscoped"] == []


def test_the_default_install_with_an_openrouter_key_names_the_verifiers_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The VPS's shape: verified answers on and `local_logprob` (both defaults), an OpenRouter key,
    no Ollama. `build_verifier` puts the Decisions API behind the local verifier, so every grounded
    turn's sources can go there without the routing preference — and the card said nothing reached it."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    snapshot = privacy_snapshot(_settings(CHIMERA_OPENROUTER_DATA_COLLECTION="deny"))
    assert snapshot["unscoped"] == ["decisions_fallback"]
    assert "decisions" in {r["provider"]: r for r in snapshot["routes"]}["openrouter"]["roles"]


@pytest.mark.parametrize(
    ("env", "key"),
    [
        ({}, True),
        ({}, False),
        ({"CHIMERA_VERIFIED_ANSWERS": "false"}, True),
        ({"CHIMERA_DECISION_BACKEND": "hosted_verbalized"}, True),
        ({"CHIMERA_DECISION_BACKEND": "openrouter_decisions"}, True),
    ],
)
def test_the_card_and_the_verifier_agree_on_whether_the_decisions_api_is_reached(
    monkeypatch: pytest.MonkeyPatch, env: dict[str, str], key: bool
) -> None:
    """Read against `build_verifier` itself, not against a restatement of its rule: the card names
    the Decisions API exactly when the chain the verifier builds holds an OpenRouter Decisions slot."""
    from chimera.fusion.verified import build_verifier

    if key:
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    settings = _settings(**env)
    verifier = build_verifier(settings)
    in_chain = verifier is not None and any(
        getattr(slot, "backend", "") == "openrouter_decisions" for slot in verifier.chain
    )
    named = bool(privacy_snapshot(settings)["unscoped"])
    if env.get("CHIMERA_DECISION_BACKEND") == "openrouter_decisions":
        # Chosen: every decision surface posts there (the `decide` tool, the band), verifier or not.
        assert named
    else:
        assert named == in_chain


def test_the_card_reports_what_was_set() -> None:
    snapshot = privacy_snapshot(
        _settings(CHIMERA_OPENROUTER_DATA_COLLECTION="deny", CHIMERA_OPENROUTER_ZDR="true")
    )
    assert snapshot["openrouter_data_collection"] == "deny"
    assert snapshot["openrouter_zdr"] is True


def test_telemetry_is_reported_on_from_either_switch_when_the_exporter_is_installed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import chimera.obs

    monkeypatch.setattr(chimera.obs, "otel_exporter_installed", lambda: True)
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    assert privacy_snapshot(_settings())["telemetry"] is False
    assert privacy_snapshot(_settings(CHIMERA_OTEL="true"))["telemetry"] is True
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://collector:4318")
    assert privacy_snapshot(_settings())["telemetry"] is True


def test_telemetry_asked_for_without_the_otel_extra_is_reported_as_nothing_exported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`configure_otel` logs "tracing stays off" and exports nothing without the extra; the card
    said "export is on: tool calls and token counts go to the configured collector"."""
    import chimera.obs

    monkeypatch.setattr(chimera.obs, "otel_exporter_installed", lambda: False)
    snapshot = privacy_snapshot(_settings(CHIMERA_OTEL="true"))
    assert snapshot["telemetry"] is False
    assert snapshot["telemetry_requested"] is True


def test_the_exporter_check_answers_for_a_missing_package_instead_of_raising(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import importlib.util

    import chimera.obs

    def missing(name: str, *_a: object) -> None:
        raise ModuleNotFoundError(name)

    monkeypatch.setattr(importlib.util, "find_spec", missing)
    assert chimera.obs.otel_exporter_installed() is False


def test_the_block_never_carries_a_credential(monkeypatch: pytest.MonkeyPatch) -> None:
    """The card is read-only facts about routing. A key set for every provider must not appear in it
    in any form — not whole, not as the hint the API-keys card uses."""
    secret = "sk-or-v1-THIS-MUST-NEVER-LEAVE-0123456789"
    monkeypatch.setenv("OPENROUTER_API_KEY", secret)
    text = repr(privacy_snapshot(_settings(CHIMERA_SEMANTIC_MEMORY="true")))
    assert secret not in text
    assert secret[-4:] not in text


# Each model setting: the env that puts it in effect, the env var that sets it, the value, and the
# provider the card must then list with the role. A setting missing from BOTH this table and
# `EXCLUDED_MODEL_SETTINGS` fails `test_every_model_setting_is_listed_or_excluded_with_a_reason`.
_MODEL_CASES: dict[str, tuple[dict[str, str], str, str, str, str]] = {
    "default_model": ({}, "CHIMERA_DEFAULT_MODEL", "sentinel/m", "sentinel", "default"),
    "weak_model": ({}, "CHIMERA_WEAK_MODEL", "sentinel/m", "sentinel", "weak"),
    "mid_model": ({}, "CHIMERA_MID_MODEL", "sentinel/m", "sentinel", "mid"),
    "orchestrator_model": ({}, "CHIMERA_ORCHESTRATOR_MODEL", "sentinel/m", "sentinel", "orchestrator"),
    "fallback_models": ({}, "CHIMERA_FALLBACK_MODELS", "sentinel/m", "sentinel", "fallback"),
    "fusion_panel": ({}, "CHIMERA_FUSION_PANEL", "sentinel/m", "sentinel", "fusion_panel"),
    "transfer_panel": ({}, "CHIMERA_TRANSFER_PANEL", "groq/llama-3.3-70b", "groq", "transfer_panel"),
    "fusion_judge": ({}, "CHIMERA_FUSION_JUDGE", "sentinel/m", "sentinel", "fusion_judge"),
    "fusion_synthesizer": ({}, "CHIMERA_FUSION_SYNTHESIZER", "sentinel/m", "sentinel", "fusion_synthesizer"),
    "embed_model": (
        {"CHIMERA_SEMANTIC_MEMORY": "true"}, "CHIMERA_EMBED_MODEL", "sentinel/e", "sentinel", "embeddings"
    ),
    "review_model": ({}, "CHIMERA_REVIEW_MODEL", "sentinel/m", "sentinel", "review"),
    "complete_model": ({}, "CHIMERA_COMPLETE_MODEL", "qwen2.5-coder:7b-base", "ollama", "completion"),
    "voice_model": ({}, "CHIMERA_VOICE_MODEL", "sentinel/m", "sentinel", "voice"),
    "voice_work_model": ({}, "CHIMERA_VOICE_WORK_MODEL", "sentinel/m", "sentinel", "voice_work"),
    "decision_model": (
        {"CHIMERA_DECISION_BACKEND": "hosted_verbalized"},
        "CHIMERA_DECISION_MODEL", "anthropic/claude-haiku-4-5", "anthropic", "decisions",
    ),
    "verified_answers_escalate_model": (
        {}, "CHIMERA_VERIFIED_ANSWERS_ESCALATE_MODEL", "gemini/gemini-2.5-pro", "gemini", "verify_escalation"
    ),
}


def test_every_model_setting_is_listed_or_excluded_with_a_reason() -> None:
    from chimera.providers.privacy import EXCLUDED_MODEL_SETTINGS

    model_shaped = {
        name for name in Settings.model_fields
        if "model" in name or "panel" in name or name in ("fusion_judge", "fusion_synthesizer")
    }
    assert model_shaped == set(_MODEL_CASES) | set(EXCLUDED_MODEL_SETTINGS)
    assert all(reason.strip() for reason in EXCLUDED_MODEL_SETTINGS.values())


@pytest.mark.parametrize("field", sorted(_MODEL_CASES))
def test_each_model_setting_puts_its_provider_on_the_card(field: str) -> None:
    extra, env, value, provider, role = _MODEL_CASES[field]
    by_name = {r["provider"]: r for r in prompt_routes(_settings(**extra, **{env: value}))}
    assert provider in by_name, f"{field}={value} sends prompts to {provider}, and the card omits it"
    assert role in by_name[provider]["roles"]


def test_the_default_decision_backend_asks_the_local_ollama() -> None:
    by_name = {r["provider"]: r for r in prompt_routes(_settings())}
    assert "decisions" in by_name["ollama"]["roles"]


def test_hosted_image_and_dictation_are_listed_under_openai_only_when_the_key_makes_them_hosted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import importlib.util

    real = importlib.util.find_spec
    # Dictation prefers a local faster-whisper when installed; hide it so the hosted route is the one.
    monkeypatch.setattr(
        importlib.util, "find_spec", lambda name, *a: None if name == "faster_whisper" else real(name, *a)
    )
    assert "openai" not in {r["provider"] for r in prompt_routes(_settings())}
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    openai = {r["provider"]: r for r in prompt_routes(_settings())}["openai"]
    assert {"image", "dictation"} <= set(openai["roles"])
    local_images = {r["provider"]: r for r in prompt_routes(_settings(CHIMERA_IMAGE_BACKEND="local"))}
    assert "image" not in local_images["openai"]["roles"]


def test_an_ollama_base_off_this_machine_is_not_called_local() -> None:
    """Ollama Cloud (or any remote Ollama) behind `ollama_chat/` used to read "local runtime" in
    green: the prefix was local, the URL the prompt goes to was not."""
    routes = {
        r["provider"]: r
        for r in prompt_routes(
            _settings(
                CHIMERA_OLLAMA_BASE_URL="https://ollama.com",
                CHIMERA_WEAK_MODEL="ollama_chat/gpt-oss:120b",
            )
        )
    }
    assert routes["ollama_chat"]["local"] is False
    assert routes["ollama_chat"]["host"] == "ollama.com"


@pytest.mark.parametrize(
    ("env", "slug", "local"),
    [
        ({}, "ollama_chat/llama3", True),
        ({"CHIMERA_OLLAMA_BASE_URL": "http://localhost:11434"}, "ollama_chat/llama3", True),
        ({"CHIMERA_OLLAMA_BASE_URL": "http://[::1]:11434"}, "ollama_chat/llama3", True),
        ({"CHIMERA_OLLAMA_BASE_URL": "http://127.0.0.1.example.com:11434"}, "ollama_chat/llama3", False),
        ({"CHIMERA_LM_STUDIO_BASE_URL": "http://10.0.0.5:1234/v1"}, "lm_studio/qwen", False),
        ({}, "lm_studio/qwen", True),
        ({"CHIMERA_API_BASE": "https://inference.example.com/v1"}, "ollama_chat/llama3", False),
        ({}, "hosted_vllm/meta/llama", False),
        ({}, "llamafile/model", True),
    ],
)
def test_local_is_read_from_the_url_the_prompt_is_sent_to(
    env: dict[str, str], slug: str, local: bool
) -> None:
    provider = slug.split("/", 1)[0]
    routes = {r["provider"]: r for r in prompt_routes(_settings(CHIMERA_WEAK_MODEL=slug, **env))}
    assert routes[provider]["local"] is local


def test_an_exported_ollama_base_wins_over_the_setting_as_it_does_in_litellm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OLLAMA_API_BASE", "https://gpu.example.net")
    routes = {r["provider"]: r for r in prompt_routes(_settings(CHIMERA_WEAK_MODEL="ollama_chat/x"))}
    assert routes["ollama_chat"]["local"] is False
    assert routes["ollama_chat"]["host"] == "gpu.example.net"


def test_only_bots_that_can_start_are_reported_as_connected() -> None:
    """An install with only Discord showed `slack: anyone` and `telegram: anyone` for bots that do not
    exist, diluting the warning about the one that runs open."""
    token = "discord-token-THIS-MUST-NEVER-LEAVE-0123456789"
    snapshot = read_config(_settings(CHIMERA_DISCORD_BOT_TOKEN=token))
    ConfigOut.model_validate(snapshot)
    assert snapshot["messaging"]["configured"] == ["discord"]
    assert set(snapshot["messaging"]["allowed_users"]) >= {"discord", "slack", "telegram"}
    assert token not in repr(snapshot["messaging"])


def test_a_platform_counts_as_connected_only_with_everything_its_constructor_requires() -> None:
    from chimera.server.allowlist import bot_configured

    assert not bot_configured(_settings(CHIMERA_SLACK_BOT_TOKEN="x"), "slack")
    assert bot_configured(_settings(CHIMERA_SLACK_BOT_TOKEN="x", CHIMERA_SLACK_APP_TOKEN="y"), "slack")
    half = _settings(CHIMERA_WHATSAPP_ACCESS_TOKEN="a", CHIMERA_WHATSAPP_PHONE_NUMBER_ID="1")
    assert not bot_configured(half, "whatsapp"), "the inbound webhook also needs the verify token"
    full = _settings(
        CHIMERA_WHATSAPP_ACCESS_TOKEN="a", CHIMERA_WHATSAPP_PHONE_NUMBER_ID="1",
        CHIMERA_WHATSAPP_VERIFY_TOKEN="v",
    )
    assert bot_configured(full, "whatsapp")
    assert read_config(_settings())["messaging"]["configured"] == []
