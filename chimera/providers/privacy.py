"""Where a prompt goes, and what the route that receives it may keep (study 29, P5.6).

Two questions an owner could not answer from the app: *which providers see my prompts* and *may
they keep them*. The first was spread over a dozen model settings (the tier ladder, the fusion cast,
the voice models, the fallbacks, the embedder behind semantic memory); the second had no setting at
all — OpenRouter forwards a request to whichever upstream route serves the model, under that route's
data policy, and nothing here could ask for a stricter one.

This module holds both answers in one place so the gateway (which sends the preference) and the
Security screen's privacy card (which reports it) cannot disagree about either.
"""

from __future__ import annotations

import os
from typing import Any

from chimera.config import Settings
from chimera.providers.discovery import is_local_model

#: The keys of OpenRouter's ``provider`` object that carry the privacy preference — and the ONLY ones
#: the gateway carries over into a caller's own route pin (``gateway._call_kwargs``).
PRIVACY_FIELDS: tuple[str, ...] = ("data_collection", "zdr")


def openrouter_privacy(settings: Settings) -> dict[str, Any]:
    """The fields to merge into an OpenRouter request's ``provider`` object; ``{}`` by default.

    Empty unless the owner asked: ``allow`` and ``zdr=false`` are OpenRouter's own defaults, and
    sending them explicitly would change every request's bytes (and its cache key) for no effect.
    The caller scopes this to ``openrouter/`` routes — another provider does not know the field.
    """
    fields: dict[str, Any] = {}
    if settings.openrouter_data_collection == "deny":
        fields["data_collection"] = "deny"
    if settings.openrouter_zdr:
        fields["zdr"] = True
    return fields


def _provider_of(model: str) -> str:
    """The route a model slug is sent to: the part before the first ``/``.

    A bare slug (``gpt-4o``) is LiteLLM's to resolve, and naming a provider for it here would be a
    guess, so it is reported as itself.
    """
    return model.split("/", 1)[0].lower() if "/" in model else model


def prompt_routes(settings: Settings) -> list[dict[str, Any]]:
    """Every provider a configured model role would send a prompt to, with the roles that use it.

    Read from configuration, not from traffic: a role nobody exercises still lists its provider,
    because a setting that WOULD send a prompt there is the thing an owner can change. The embedder
    counts only while semantic memory is on — it is the one role whose input is the owner's stored
    memory rather than a turn. Ordered by first appearance, so the default model's provider comes
    first. Every model setting is either here or in ``EXCLUDED_MODEL_SETTINGS`` with its reason
    (a test holds that), because a card titled "who receives prompts" that silently skips one says
    the provider behind it receives nothing.
    """
    roles = _model_roles(settings) + _surface_roles(settings)
    routes: dict[str, dict[str, Any]] = {}
    for role, model in roles:
        slug = (model or "").strip()
        if slug:
            local, host = _reach(slug, settings)
            _add(routes, _provider_of(slug), local, role, host)
    if _decisions_api_use(settings):
        _add(routes, "openrouter", False, "decisions")
    return list(routes.values())


# Where LiteLLM sends a keyless-runtime prefix when no `api_base` is passed: the env var it reads
# first (the gateway exports Ollama's and LM Studio's from Settings only when unset), then the
# Settings field or LiteLLM's own default. `None` for the base = no server is asked at all.
_RUNTIME_BASES: dict[str, tuple[str, str | None]] = {
    "ollama": ("OLLAMA_API_BASE", "ollama_base_url"),
    "ollama_chat": ("OLLAMA_API_BASE", "ollama_base_url"),
    "lm_studio": ("LM_STUDIO_API_BASE", "lm_studio_base_url"),
    "llamafile": ("LLAMAFILE_API_BASE", "=http://127.0.0.1:8080/v1"),
    "hosted_vllm": ("HOSTED_VLLM_API_BASE", None),
}


def _reach(slug: str, settings: Settings) -> tuple[bool, str]:
    """``(local, host)`` for a model slug: local only when the prompt stays on this machine.

    The prefix alone said ``ollama_chat/`` is local, and with CHIMERA_OLLAMA_BASE_URL at Ollama
    Cloud or a remote server the card showed a green "local runtime" for a prompt that left the
    machine. So a keyless-runtime prefix is local only when the URL it is actually sent to — the
    custom endpoint when one is set (it applies to every call), else the runtime's own base — is a
    loopback address; otherwise it is remote, with the host it goes to. ``vllm/`` without an endpoint
    is LiteLLM's in-process engine, and an unknown base (``hosted_vllm/`` with nothing set) cannot be
    shown to be local, so it is not called local. ``host`` is ``""`` for a local or a hosted route.
    """
    if not is_local_model(slug):
        return False, ""
    prefix = _provider_of(slug)
    url = (settings.api_base or "").strip()
    if not url:
        if prefix == "vllm":
            return True, ""
        env_var, source = _RUNTIME_BASES.get(prefix, ("", None))
        url = os.environ.get(env_var, "").strip() if env_var else ""
        if not url and source is not None:
            url = source[1:] if source.startswith("=") else str(getattr(settings, source, "") or "")
    host = _host_of(url)
    if _is_loopback(host):
        return True, ""
    return False, host


def _host_of(url: str) -> str:
    from urllib.parse import urlsplit

    try:
        return (urlsplit(url.strip()).hostname or "").lower()
    except ValueError:
        return ""


def _is_loopback(host: str) -> bool:
    """Parsed as an address, never matched as a prefix (`127.0.0.1.example.com` is not loopback);
    an empty or unparseable host is NOT local — the card must not call an unknown place this machine."""
    import ipaddress

    if not host:
        return False
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


#: Model-shaped settings the card deliberately does not list, and why.
EXCLUDED_MODEL_SETTINGS: dict[str, str] = {
    "image_model_local": "runs in-process through diffusers; the prompt never leaves the machine",
    "fusion_blind_panel": "a switch, not a model",
    "fusion_panel_temperatures": "sampling temperatures, not models",
}


def _add(
    routes: dict[str, dict[str, Any]], name: str, local: bool, role: str, host: str = ""
) -> None:
    entry = routes.setdefault(name, {"provider": name, "local": local, "host": host, "roles": []})
    # One remote slug makes the provider remote: "local" must hold for every role listed under it.
    entry["local"] = bool(entry["local"]) and local
    if host and not entry["host"]:
        entry["host"] = host
    if role not in entry["roles"]:
        entry["roles"].append(role)


def _model_roles(settings: Settings) -> list[tuple[str, str]]:
    """The chat-model settings: the tier ladder, the casts, the voice and review models."""
    ladder = settings.tier_ladder()
    roles: list[tuple[str, str]] = [
        ("default", settings.default_model),
        ("weak", ladder.weak),
        ("mid", ladder.mid),
        ("top", ladder.top),
        ("orchestrator", settings.orchestrator_model),
        *(("fallback", m) for m in settings.fallback_models),
        *(("fusion_panel", m) for m in settings.fusion_panel),
        ("fusion_judge", settings.fusion_judge),
        ("fusion_synthesizer", settings.fusion_synthesizer),
        # Skill transfer asks each of these models in turn (`chimera/evolution/context.py`).
        *(("transfer_panel", m) for m in settings.transfer_panel),
        ("voice", settings.voice_model),
        ("voice_work", settings.voice_work_model),
        ("review", settings.review_model),
    ]
    if settings.semantic_memory:
        roles.append(("embeddings", settings.embed_model))
    return roles


def _surface_roles(settings: Settings) -> list[tuple[str, str]]:
    """The roles outside the chat ladder that still carry a turn's text somewhere.

    Each mirrors the code that builds the call, so the card names the provider that code would use:
    the decision backend's model (`chimera/decisions/factory.py: build_backend`), the verified-answer
    escalation (`verified.escalation_model`, fed the turn's sources and question), the editor's inline
    completion (Ollama, bare tag), hosted image generation and hosted dictation (OpenAI, by key).
    """
    from chimera.decisions.factory import default_model_for

    roles: list[tuple[str, str]] = []
    backend = (settings.decision_backend or "local_logprob").strip()
    model = (settings.decision_model or "").strip()
    if backend == "local_logprob":
        # A bare Ollama tag, asked at `ollama_base_url`.
        roles.append(("decisions", f"ollama/{model or default_model_for(settings, backend)}"))
    elif backend == "hosted_verbalized":
        # Through the gateway, so an `openrouter/` slug here DOES carry the preference.
        roles.append(("decisions", model or default_model_for(settings, backend)))
    if settings.verified_answers:
        from chimera.fusion.verified import escalation_model

        roles.append(("verify_escalation", escalation_model(settings)))
    if (settings.complete_model or "").strip():
        roles.append(("completion", f"ollama/{settings.complete_model.strip()}"))
    image = (settings.image_backend or "auto").strip().lower()
    if image == "hosted" or (image == "auto" and settings.key_pool("openai")):
        # `chimera/tools/media.py`: the prompt goes to OpenAI's images endpoint.
        roles.append(("image", "openai/gpt-image-1"))
    from chimera.api.attachments import dictation_support

    if dictation_support(settings) == ("yes", "openai"):
        roles.append(("dictation", "openai/whisper-1"))
    return roles


def _decisions_api_use(settings: Settings) -> str:
    """The verifier's own predicate (`chimera/fusion/verified.py`), imported late: that module pulls
    in the decision stack, which the gateway importing this one must not pay for."""
    from chimera.fusion.verified import decisions_api_use

    return decisions_api_use(settings)


def privacy_snapshot(settings: Settings) -> dict[str, Any]:
    """The privacy card's facts that no other block of ``GET /api/config`` already carries.

    Memory, the egress list and the bot allowlists are read by the card from their own blocks; one
    copy of each is what keeps the card and the row that changes it from disagreeing.
    """
    return {
        "openrouter_data_collection": settings.openrouter_data_collection,
        "openrouter_zdr": settings.openrouter_zdr,
        "routes": prompt_routes(settings),
        # The OpenTelemetry exporter turns on from either switch (`chimera/obs.py`), so the card
        # reports the effective state, not the setting alone.
        "telemetry": bool(settings.otel) or bool(os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")),
        # A surface that reaches OpenRouter WITHOUT the preference above. Named rather than hidden:
        # an owner who set `deny` would otherwise believe every call carries it. `decisions` when the
        # Decisions API is the chosen backend; `decisions_fallback` when it only stands behind the
        # local verifier — the default install with an OpenRouter key, so the common case.
        "unscoped": _unscoped(settings),
    }


def _unscoped(settings: Settings) -> list[str]:
    use = _decisions_api_use(settings)
    if use == "chosen":
        return ["decisions"]
    if use == "fallback":
        return ["decisions_fallback"]
    return []
