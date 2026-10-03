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


def test_telemetry_is_reported_on_from_either_switch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    assert privacy_snapshot(_settings())["telemetry"] is False
    assert privacy_snapshot(_settings(CHIMERA_OTEL="true"))["telemetry"] is True
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://collector:4318")
    assert privacy_snapshot(_settings())["telemetry"] is True


def test_the_block_never_carries_a_credential(monkeypatch: pytest.MonkeyPatch) -> None:
    """The card is read-only facts about routing. A key set for every provider must not appear in it
    in any form — not whole, not as the hint the API-keys card uses."""
    secret = "sk-or-v1-THIS-MUST-NEVER-LEAVE-0123456789"
    monkeypatch.setenv("OPENROUTER_API_KEY", secret)
    text = repr(privacy_snapshot(_settings(CHIMERA_SEMANTIC_MEMORY="true")))
    assert secret not in text
    assert secret[-4:] not in text
