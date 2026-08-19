"""Runtime configuration for Chimera.

Settings are read from environment variables and an optional ``.env`` file.
Nothing here requires a key at import time — the agent only needs credentials for
the providers it actually calls (see :mod:`chimera.providers.gateway`).
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterable
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

# stdlib logging rather than `chimera.telemetry.get_logger`: telemetry reads settings, so importing
# it here is a cycle. Same logger tree either way — this lands under `chimera.config` like the rest.
_log = logging.getLogger("chimera.config")

#: The two vocabularies that shared one env var until they were split. Named here rather than
#: inline so each validator can recognise the OTHER side and say which variable the value belongs
#: to — a message that only says "invalid" sends someone to the wrong file.
_POSTURE_WORDS = frozenset({"always", "suspicious", "never"})  # CHIMERA_APPROVAL
_GOVERNANCE_WORDS = frozenset({"ask", "allow", "deny"})  # CHIMERA_APPROVAL_MODE

if TYPE_CHECKING:
    from chimera.providers.catalog import TierLadder

# Two of these three were WITHDRAWN by their providers and nobody noticed until the catalogue was
# audited on 2026-08-18 — so the default fusion panel, the feature whose entire premise is several
# independent models answering, was convening one model and two 404s. A default that names somebody
# else's product decays on their schedule; `tests/test_catalog_is_live.py` now checks these too.
_DEFAULT_PANEL = [
    "openrouter/anthropic/claude-opus-5",
    "openrouter/openai/gpt-5.5",
    "openrouter/google/gemini-3.1-pro-preview",
]
# The judge must not be a panelist. It shipped as `_DEFAULT_PANEL[0]` — the same slug, verbatim —
# which made the default fusion self-evaluating in the one place this project claims to have an
# independent signal rather than a self-report. Nothing guarded it; `validate_fusion_roles` below
# does now. DeepSeek-R1 is a fourth vendor (the panel is Anthropic/OpenAI/Google) and is reasoning-
# tuned, which is what judging asks for.
_DEFAULT_JUDGE = "openrouter/deepseek/deepseek-r1"
# Spelled out rather than reusing `_DEFAULT_JUDGE`, which is what it did before. Changing the judge
# would otherwise have moved the synthesiser too, silently — the synthesiser's job is composition,
# not evaluation, so it is a separate decision and stays where it was.
#
# It is still `_DEFAULT_PANEL[0]`, and that is a milder version of the same smell: the model that
# wrote one of the candidate answers also writes the final one. Left alone deliberately — the
# measured finding was about the judge — and recorded here so it is visible instead of buried.
_DEFAULT_SYNTHESIZER = "openrouter/anthropic/claude-opus-5"

# Panel used only to TEST whether a learned skill transfers — never to reason. Transfer asks
# "does this run and pass somewhere else?", which is a diversity question, not a capability one:
# a skill that survives a cheap model is better evidence of generality than one that needs a
# frontier model. Kept separate from `fusion_panel` so widening the statistical sample does not
# multiply the cost of every fused turn. Nine models give a usable n; three do not (a flawless
# 3/3 earns a 0.344 lower bound, so a 0.5 gate can never be met by any result at all).
_DEFAULT_TRANSFER_PANEL = [
    "openrouter/deepseek/deepseek-chat-v3.1",
    "openrouter/deepseek/deepseek-r1",
    "openrouter/google/gemini-2.5-flash",
    "openrouter/mistralai/mistral-small-3.2-24b-instruct",
    "openrouter/moonshotai/kimi-k2",
    "openrouter/openai/gpt-5.6-luna",
    "openrouter/qwen/qwen3-max",
    "openrouter/qwen/qwen3-coder",
    "openrouter/z-ai/glm-4.6",
]


class Settings(BaseSettings):
    """Process-wide configuration, populated from env / ``.env``."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Provider keys (each optional; LiteLLM also reads these directly) ---
    openrouter_api_key: str | None = Field(default=None, validation_alias="OPENROUTER_API_KEY")
    openai_api_key: str | None = Field(default=None, validation_alias="OPENAI_API_KEY")
    anthropic_api_key: str | None = Field(default=None, validation_alias="ANTHROPIC_API_KEY")
    gemini_api_key: str | None = Field(default=None, validation_alias="GEMINI_API_KEY")
    deepseek_api_key: str | None = Field(default=None, validation_alias="DEEPSEEK_API_KEY")

    # --- Credential pools: comma-separated keys per provider, rotated round-robin ---
    openrouter_keys: Annotated[list[str], NoDecode] = Field(
        default_factory=list, validation_alias="CHIMERA_OPENROUTER_KEYS"
    )
    openai_keys: Annotated[list[str], NoDecode] = Field(
        default_factory=list, validation_alias="CHIMERA_OPENAI_KEYS"
    )
    anthropic_keys: Annotated[list[str], NoDecode] = Field(
        default_factory=list, validation_alias="CHIMERA_ANTHROPIC_KEYS"
    )
    gemini_keys: Annotated[list[str], NoDecode] = Field(
        default_factory=list, validation_alias="CHIMERA_GEMINI_KEYS"
    )
    deepseek_keys: Annotated[list[str], NoDecode] = Field(
        default_factory=list, validation_alias="CHIMERA_DEEPSEEK_KEYS"
    )

    # --- Optional feature credentials (pre-set slots; set only what you use) ---
    tavily_api_key: str | None = Field(default=None, validation_alias="TAVILY_API_KEY")
    # Optional Firecrawl fallback for the scrape/extract tools: used only for pages the built-in
    # engine can't fetch (heavy anti-bot). Set FIRECRAWL_API_KEY to enable; unset = engine-only.
    firecrawl_api_key: str | None = Field(default=None, validation_alias="FIRECRAWL_API_KEY")
    brave_api_key: str | None = Field(default=None, validation_alias="BRAVE_API_KEY")
    serpapi_key: str | None = Field(default=None, validation_alias="SERPAPI_API_KEY")
    x_bearer_token: str | None = Field(default=None, validation_alias="X_BEARER_TOKEN")
    stability_api_key: str | None = Field(default=None, validation_alias="STABILITY_API_KEY")
    elevenlabs_api_key: str | None = Field(default=None, validation_alias="ELEVENLABS_API_KEY")
    spotify_client_id: str | None = Field(default=None, validation_alias="SPOTIFY_CLIENT_ID")
    spotify_client_secret: str | None = Field(default=None, validation_alias="SPOTIFY_CLIENT_SECRET")

    # --- Default single model (Tier 1 / cheap tasks) ---
    #
    # DeepSeek V3.1 rather than a frontier model, and the reason is what a default IS: the model a
    # fresh install spends money on before anyone has made a decision. This one is the `mid` rung of
    # every cost preset, the one this repo's own benches ran on, and — at the live OpenRouter list
    # price when this changed — $0.25/$0.95 per 1M against GPT-5.5's $5.00/$30.00. Twenty times
    # cheaper in, thirty times cheaper out, for the questions a first conversation asks.
    #
    # It is a floor, not a ceiling: the composer's model picker changes it per conversation and
    # offers to make any pick the standing default, and `CHIMERA_DEFAULT_MODEL` still wins over
    # this. Starting expensive and asking people to notice is the wrong way round — the bill arrives
    # before the knowledge that there was a choice.
    #
    # MUST stay in sync with the OpenRouter entry in `chimera.providers.catalog.PROVIDERS`: the
    # wizard SHOWS that suggestion without writing it when the user leaves it alone, so a mismatch
    # puts one slug on screen and runs another.
    default_model: str = Field(
        default="openrouter/deepseek/deepseek-chat-v3.1", validation_alias="CHIMERA_DEFAULT_MODEL"
    )

    # --- Model tiers (M16): weak -> mid -> top, vendor-agnostic. Any LiteLLM/OpenRouter
    # slug can occupy any role. Empty string = "let cost_mode decide" (see
    # chimera/providers/catalog.py); a non-empty value is an explicit user choice and
    # ALWAYS wins over the mode. ---
    weak_model: str = Field(default="", validation_alias="CHIMERA_WEAK_MODEL")
    mid_model: str = Field(default="", validation_alias="CHIMERA_MID_MODEL")
    orchestrator_model: str = Field(default="", validation_alias="CHIMERA_ORCHESTRATOR_MODEL")

    # --- Cost mode: how the tier ladder is filled when models aren't pinned.
    # "cheap" = weak-first aggressive; "balanced" = economic defaults; "premium" =
    # frontier everywhere; "auto" (default) = prioritizes the MID tier as the entry
    # point and lets the cascade climb/descend from there. ---
    cost_mode: str = Field(default="auto", validation_alias="CHIMERA_COST_MODE")

    # --- Cascade routing (M16-A6): weak -> gate -> mid -> gate -> fusion. Off by
    # default; `--cascade` on solve/chat or CHIMERA_CASCADE=1 enables. ---
    cascade: bool = Field(default=False, validation_alias="CHIMERA_CASCADE")

    # --- Per-delegation token budget for hierarchical orchestration (M16-A4),
    # enforced by the harness (BudgetedBackend), not by prompt instructions. ---
    delegation_budget: int = Field(default=8000, validation_alias="CHIMERA_DELEGATION_BUDGET")

    # --- Custom endpoint for self-hosted/OpenAI-compatible servers (Ollama, vLLM) ---
    api_base: str | None = Field(default=None, validation_alias="CHIMERA_API_BASE")

    # --- Fallback chain: tried in order if the primary model errors ---
    fallback_models: Annotated[list[str], NoDecode] = Field(
        default_factory=list, validation_alias="CHIMERA_FALLBACK_MODELS"
    )

    # --- Fusion engine (panel -> judge -> synthesizer) ---
    fusion_panel: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: list(_DEFAULT_PANEL), validation_alias="CHIMERA_FUSION_PANEL"
    )
    # Skill-transfer test panel. Proposals still come from `fusion_panel` (strong models write
    # better skills); only the pass/fail sampling happens here, on cheap models.
    transfer_panel: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: list(_DEFAULT_TRANSFER_PANEL),
        validation_alias="CHIMERA_TRANSFER_PANEL",
    )
    fusion_judge: str = Field(default=_DEFAULT_JUDGE, validation_alias="CHIMERA_FUSION_JUDGE")
    fusion_synthesizer: str = Field(
        default=_DEFAULT_SYNTHESIZER, validation_alias="CHIMERA_FUSION_SYNTHESIZER"
    )

    # --- Selective fusion: run a probe of the first `fusion_probe_k` panel models; if
    # they agree closely (a cheap local text-similarity check, no extra model call), skip
    # the rest of the panel AND the judge and synthesize from the agreeing answers;
    # otherwise escalate to the full panel -> judge -> synthesizer. Disagreement therefore
    # costs the same as full fusion; agreement is cheaper. ON by default: across 3 runs of
    # the `fusion-bench` hard suite it cut tokens ~20-28% and never lost accuracy on any
    # turn it actually short-circuited (16/16 correct). Set to "full" to disable. ---
    fusion_mode: str = Field(default="selective", validation_alias="CHIMERA_FUSION_MODE")
    fusion_probe_k: int = Field(default=2, validation_alias="CHIMERA_FUSION_PROBE_K")
    fusion_agreement_threshold: float = Field(
        default=0.8, validation_alias="CHIMERA_FUSION_AGREEMENT"
    )
    # --- Task-typed aggregation (MALLM, arXiv 2607.05477): when on, a logic/single-answer task
    # (arithmetic, counting, multiple-choice, true/false) on which the panel reaches a clear
    # majority is aggregated by VOTE, skipping the judge+synthesizer — a correct minority answer
    # isn't averaged away, and it's cheaper. Off by default and conservative: knowledge/open tasks,
    # and any logic task without a panel majority, still use judge -> synthesizer. ---
    fusion_task_typed: bool = Field(default=False, validation_alias="CHIMERA_FUSION_TASK_TYPED")
    # --- Diversity sampling (how_to_generate study): per-panelist decode spread. A comma-separated
    # list of temperatures (e.g. "0.2,0.5,0.7,0.9") — panelist i samples at temps[i % len], widening
    # the candidate set the judge/synthesizer selects from (one low-temp anchor + higher-temp
    # explorers) at near-zero cost. Empty (default) = every panelist at the single 0.3, unchanged.
    # Measure the lift with `fusion-bench` before making a spread the default; `panel_diversity()` in
    # the route_meta reports whether the spread actually widened the answers. ---
    fusion_panel_temperatures: Annotated[list[float], NoDecode] = Field(
        default_factory=list, validation_alias="CHIMERA_FUSION_PANEL_TEMPS"
    )

    # --- Behaviour ---
    log_level: str = Field(default="INFO", validation_alias="CHIMERA_LOG_LEVEL")
    home: Path = Field(default=Path(".chimera"), validation_alias="CHIMERA_HOME")

    # --- Exact-match completion cache for tool-free turns (HORIZON prompt caching) ---
    cache: bool = Field(default=False, validation_alias="CHIMERA_CACHE")
    prompt_cache: bool = Field(default=False, validation_alias="CHIMERA_PROMPT_CACHE")
    """Opt-in: mark the stable system prefix with a provider cache breakpoint so the
    single agent / worker fleet reuse it at the cache read rate. Providers that cache
    automatically (OpenAI, DeepSeek) are left untouched; only breakpoint-requiring
    families (Anthropic/Claude) get an explicit cache_control marker."""

    # --- Browser tool: run Chromium headless (default) or headful for debugging. ---
    browser_headless: bool = Field(default=True, validation_alias="CHIMERA_BROWSER_HEADLESS")

    # --- Image generation backend: 'auto' (hosted if an OpenAI key is set, else local diffusers),
    # 'hosted' (OpenAI), or 'local' (run FLUX/SD via the imagegen-local extra — heavy, GPU). ---
    image_backend: str = Field(default="auto", validation_alias="CHIMERA_IMAGE_BACKEND")
    image_model_local: str = Field(
        default="black-forest-labs/FLUX.1-schnell",  # Apache-2.0 weights — commercially safe
        validation_alias="CHIMERA_IMAGE_MODEL_LOCAL",
    )

    # --- Long-term memory backend: json (default, zero-dep) or sqlite (FTS5 full-text) ---
    memory_backend: str = Field(default="json", validation_alias="CHIMERA_MEMORY_BACKEND")

    # --- Opt-in semantic memory recall: embed facts + query and rank by cosine, so a
    # paraphrase with no shared token still retrieves the right fact (the gap memory-bench
    # exposes for pure keyword search). Off by default — needs an embeddings-capable key.
    # On any embedder error, search falls back to the keyword/FTS path (never a hard fail). ---
    semantic_memory: bool = Field(default=False, validation_alias="CHIMERA_SEMANTIC_MEMORY")
    # M18-4: birth newly-learned skills 'provisional' (retrieved on probation, then auto-promoted on a
    # measured track record or demoted on regression). Off = new skills go straight to 'active' as before.
    provisional_skills: bool = Field(default=False, validation_alias="CHIMERA_PROVISIONAL_SKILLS")
    embed_model: str = Field(
        default="openrouter/openai/text-embedding-3-small",
        validation_alias="CHIMERA_EMBED_MODEL",
    )

    # --- Opt-in: at the end of a chat session, if memory has grown past
    # `memory_budget`, consolidate near-duplicate facts with the model (bounded cost:
    # skipped entirely while memory is small). Off by default. ---
    auto_consolidate: bool = Field(default=False, validation_alias="CHIMERA_AUTO_CONSOLIDATE")
    memory_budget: int = Field(default=200, validation_alias="CHIMERA_MEMORY_BUDGET")

    # --- Opt-in: at app start, load the tools of the MCP servers configured in `.chimera/mcp.json`
    # into the agent's registry (each connected with a per-server timeout, a broken one skipped so it
    # can't break boot). Off by default: boot stays fast and spawns no subprocess. Toggling it needs a
    # restart to take effect. MCP tool output is untrusted (the `untrusted_output` flag flows to
    # governance). Configure servers with `chimera mcp add` or the desktop MCP screen. ---
    mcp_autoload: bool = Field(default=False, validation_alias="CHIMERA_MCP_AUTOLOAD")

    # --- Auto-fuse error-sensitive turns in solve/crew without an explicit --fuse.
    # Off by default (fusion costs 2-3x); when on, the cost-aware router still keeps
    # cheap/tool turns single-model and only fuses deep or error-sensitive ones. ---
    auto_fuse: bool = Field(default=False, validation_alias="CHIMERA_AUTO_FUSE")

    # --- TRS skill cards (Improvement #1): retrieve learned reasoning cards (BM25 over
    # name+description+triggers) and inject the top-k into the worker's reasoning context.
    # Off by default (an experiment — injection can raise cost if retrieval misfires);
    # measure with `chimera skillcard-bench` before enabling. ---
    #: Arm B of `bench/edit_tools`: a counted, multi-file batch edit in one tool call.
    #:
    #: Off until the bench says otherwise. Arm A of that bench IS "today's tool surface", so turning
    #: this on by default would delete the control arm before the comparison ran — and the schema
    #: rides in every prompt for the rest of the run, a cost this project has watched swallow a
    #: gain before (`bench/skillcard/RESULTS.md`: +16.7pp, not significant, at +300% tokens).
    edit_batch: bool = Field(default=False, validation_alias="CHIMERA_EDIT_BATCH")
    skill_cards: bool = Field(default=False, validation_alias="CHIMERA_SKILL_CARDS")
    skill_cards_k: int = Field(default=1, validation_alias="CHIMERA_SKILL_CARDS_K")
    # Relevance gate + render budget (M19-A1 cost reduction): inject a card only when it shares at
    # least ``min_overlap`` query terms (so a task with no strong match pays ZERO extra tokens instead
    # of dragging in ~irrelevant cards), and cap each card at ``max_lines``. These crush the token
    # overhead that failed the skillcard flip gate; see bench/skillcard/RESULTS.md.
    skill_cards_min_overlap: int = Field(default=2, validation_alias="CHIMERA_SKILL_CARDS_MIN_OVERLAP")
    skill_cards_max_lines: int = Field(default=3, validation_alias="CHIMERA_SKILL_CARDS_MAX_LINES")
    # M19-A1 flip-point: when on, card READING couples to skill EVOLVING (a run that can mint a
    # skill also reads the retrieved ones), instead of the independent `skill_cards` toggle. Stays
    # OFF by default — the paired A/B is in (bench/skillcard/RESULTS.md, goldilocks n=12): accuracy
    # +16.7pp but NOT significant (CI includes 0) and +300% tokens, so it fails the registered
    # flip gate and reading cards stays opt-in. Pair with CHIMERA_PROVISIONAL_SKILLS + the lifecycle
    # cron if you do opt in, so a misfiring card is born on probation and auto-demoted.
    skill_cards_couple_read: bool = Field(
        default=False, validation_alias="CHIMERA_SKILL_CARDS_READ"
    )

    # --- ACE playbook curation from errors (Level-2 P3): when curating the playbook after a run,
    # feed the curator the actual error evidence — the failing verifier output and the diff that
    # fixed it — not just verdict+final-answer. Blind-to-failure curation produces platitudes; the
    # evidence lets it distill process pitfalls ("run the failing test first", "re-check a second
    # case", "re-read the docstring for quiet clauses"). On by default: strictly more signal for a
    # curator already instructed to generalise. Set 0 to ablate against the verdict-only baseline. ---
    playbook_curate_from_errors: bool = Field(
        default=True, validation_alias="CHIMERA_PLAYBOOK_CURATE_FROM_ERRORS"
    )

    # --- How the collective skill-accept gate scores cross-model transfer: "point" (the
    # raw pass fraction, default) or "wilson" (the lower Wilson confidence bound, so a
    # lucky small-sample pass no longer clears the threshold). "wilson" is strict on tiny
    # panels — use it with panels >= ~5, or lower CHIMERA_SKILL_MIN_TRANSFER. ---
    skill_accept_mode: str = Field(default="point", validation_alias="CHIMERA_SKILL_ACCEPT_MODE")

    # --- SkillCoach process filter for `chimera evolve export`: keep only trajectories
    # whose step-following score >= this (so a lucky success with failed tool steps is not
    # trained on). 0.0 = off (default). ---
    sft_min_process: float = Field(default=0.0, validation_alias="CHIMERA_SFT_MIN_PROCESS")

    # --- Compact tool schemas at advertise-time (Improvement #5a): strip annotation
    # noise and trim parameter prose from the `tools=` payload re-sent every ReAct step.
    # Semantics preserved (name/type/required/enum kept). Off by default; the win is
    # largest with verbose MCP/OpenAPI toolsets — measure with `chimera schema-bench`. ---
    compact_schemas: bool = Field(default=False, validation_alias="CHIMERA_COMPACT_SCHEMAS")

    # --- Messaging bot tokens (only needed for the matching `chimera serve --<platform>`) ---
    discord_bot_token: str | None = Field(default=None, validation_alias="CHIMERA_DISCORD_BOT_TOKEN")
    telegram_bot_token: str | None = Field(default=None, validation_alias="CHIMERA_TELEGRAM_BOT_TOKEN")
    slack_bot_token: str | None = Field(default=None, validation_alias="CHIMERA_SLACK_BOT_TOKEN")
    slack_app_token: str | None = Field(default=None, validation_alias="CHIMERA_SLACK_APP_TOKEN")
    whatsapp_access_token: str | None = Field(default=None, validation_alias="CHIMERA_WHATSAPP_ACCESS_TOKEN")
    whatsapp_phone_number_id: str | None = Field(
        default=None, validation_alias="CHIMERA_WHATSAPP_PHONE_NUMBER_ID"
    )
    whatsapp_verify_token: str | None = Field(
        default=None, validation_alias="CHIMERA_WHATSAPP_VERIFY_TOKEN"
    )
    whatsapp_app_secret: str | None = Field(
        default=None, validation_alias="CHIMERA_WHATSAPP_APP_SECRET"
    )  # set to verify the inbound webhook's X-Hub-Signature-256 HMAC
    # Optional bearer token guarding the state-changing HTTP endpoints (/a2a, /chat, /webhook/*).
    # Unset = no auth (fine for localhost); set it before exposing the server to a network.
    server_token: str | None = Field(default=None, validation_alias="CHIMERA_SERVER_TOKEN")
    # Origins allowed to call this instance from a browser — comma-separated, empty by default.
    #
    # Only the desktop app pointed at a REMOTE Chimera needs this: it is served from its own
    # loopback sidecar, so every call to another host is cross-origin and the browser drops the
    # response unless the server names that origin. Serving the bundled SPA is same-origin and
    # needs nothing, which is why the default stays closed.
    #
    # This is not the authorization boundary and must not be read as one. CORS decides which page
    # may *read* a response; it decides nothing about who may call. `server_token` is the gate —
    # naming an origin here without setting a token does not protect the instance, it just makes an
    # unprotected instance reachable from a browser as well as from curl.
    allowed_origins: str = Field(default="", validation_alias="CHIMERA_ALLOWED_ORIGINS")
    signal_api_url: str | None = Field(default=None, validation_alias="CHIMERA_SIGNAL_API_URL")
    signal_number: str | None = Field(default=None, validation_alias="CHIMERA_SIGNAL_NUMBER")

    # --- Email (SMTP) for the send_email reference tool ---
    smtp_host: str | None = Field(default=None, validation_alias="CHIMERA_SMTP_HOST")
    smtp_port: int = Field(default=587, validation_alias="CHIMERA_SMTP_PORT")
    smtp_user: str | None = Field(default=None, validation_alias="CHIMERA_SMTP_USER")
    smtp_password: str | None = Field(default=None, validation_alias="CHIMERA_SMTP_PASSWORD")
    smtp_from: str | None = Field(default=None, validation_alias="CHIMERA_SMTP_FROM")

    # --- IMAP for the read_email reference tool ---
    imap_host: str | None = Field(default=None, validation_alias="CHIMERA_IMAP_HOST")
    imap_port: int = Field(default=993, validation_alias="CHIMERA_IMAP_PORT")
    imap_user: str | None = Field(default=None, validation_alias="CHIMERA_IMAP_USER")
    imap_password: str | None = Field(default=None, validation_alias="CHIMERA_IMAP_PASSWORD")

    # --- Default iCalendar feed for the calendar_events reference tool ---
    calendar_ics_url: str | None = Field(default=None, validation_alias="CHIMERA_CALENDAR_ICS_URL")

    # --- Execution sandbox for the shell tool (local = host, docker = isolated) ---
    sandbox: str = Field(default="local", validation_alias="CHIMERA_SANDBOX")
    sandbox_image: str = Field(
        default="python:3.12-slim", validation_alias="CHIMERA_SANDBOX_IMAGE"
    )
    # Keep `<think>` blocks in the answer instead of filtering them out.
    #
    # Off by default because a reasoning block in `message.content` is never what the caller asked
    # for — it lands in the terminal, in the desktop transcript, and in whatever consumes the answer
    # next. The escape exists because a filter with no way off is worse than the noise it removes:
    # someone working ON reasoning tags needs the raw stream, and finding out that the tool silently
    # ate their data is a bad afternoon.
    keep_think: bool = Field(default=False, validation_alias="CHIMERA_KEEP_THINK")
    # Optional OCI runtime for the docker sandbox (e.g. runsc = gVisor); empty = daemon default.
    sandbox_runtime: str = Field(default="", validation_alias="CHIMERA_SANDBOX_RUNTIME")
    # Container network. "none" (the default) is the isolation the sandbox is for; "bridge" exists
    # because a task that has to `pip install` cannot run without it, and the honest answer to "how
    # many tasks need it" is a number nobody has measured yet. Setting this is what makes that
    # measurable rather than theoretical.
    #
    # NOT an egress allowlist, and that is not an omission: Chimera is a pip install on a laptop,
    # and there is no DOCKER-USER chain to hook on Docker Desktop for Windows or macOS. If the
    # adoption number ever justifies filtering, the route is an egress proxy in a compose file,
    # never iptables on the host.
    sandbox_network: str = Field(default="none", validation_alias="CHIMERA_SANDBOX_NETWORK")
    # Container limits. Memory was already a constructor parameter with no way to set it.
    sandbox_memory: str = Field(default="512m", validation_alias="CHIMERA_SANDBOX_MEMORY")
    sandbox_cpus: str = Field(default="2", validation_alias="CHIMERA_SANDBOX_CPUS")
    sandbox_pids_limit: int = Field(default=256, validation_alias="CHIMERA_SANDBOX_PIDS")
    # Posture for running the agent's commands/code ON THE HOST (i.e. sandbox=local). Because most
    # `pip install` users have no Docker, host execution is the common path — so the model deciding to
    # run a shell command must not silently execute on the machine. Values:
    #   ask   (default) — in an interactive terminal, confirm each host command; headless (no TTY),
    #                     REFUSE, explaining how to opt in. "Ask" means a human decides, and
    #                     unattended there is no human — assuming consent made `ask` mean `allow`
    #                     on every server/cron/CI surface, which is where it matters most.
    #   allow           — run on the host without asking (the pre-2026-07 behaviour; explicit opt-in,
    #                     and what an unattended deployment that genuinely needs host exec should set).
    #   deny            — never run on the host; require CHIMERA_SANDBOX=docker.
    # Ignored when the sandbox is an isolated container (nothing to confirm).
    host_exec: str = Field(default="ask", validation_alias="CHIMERA_HOST_EXEC")

    # The deployment's own posture — how far the agent may reach, and when it stops to ask. Both
    # empty by default, and that emptiness is load-bearing: "" means "this deployment states no
    # posture", which is the behaviour every existing caller has (a request that sends none gets
    # nothing denied). Setting either makes it a FLOOR — it unions with the request's posture rather
    # than replacing it, so a client cannot widen what the owner narrowed. Same rule, same reason, as
    # CHIMERA_TOOL_DENYLIST.
    #
    # reach:    read_only | workspace | workspace_shell   ("" = state nothing)
    # approval: always | suspicious | never               ("" = state nothing)
    reach: str = Field(default="", validation_alias="CHIMERA_REACH")
    approval: str = Field(default="", validation_alias="CHIMERA_APPROVAL")

    # Per-request deadline (seconds) for every model call. A provider that accepts the connection
    # and then never answers would otherwise stall a run forever — step/attempt budgets bound how
    # many calls happen, not how long one may take. Generous by default so a long legitimate
    # completion is not cut short; 0 disables the bound (the pre-2026-07 behaviour).
    request_timeout: float = Field(default=600.0, validation_alias="CHIMERA_REQUEST_TIMEOUT")

    # The outermost deadline (seconds) for ONE parallel fan-out of isolated agents — the batch
    # behind POST /api/agents, `chimera solve-batch` and parallel kanban dispatch. Not the same
    # bound as CHIMERA_REQUEST_TIMEOUT above, which bounds a single model CALL: a worker can stop
    # making progress with no call in flight at all (a shell tool that never returns, a lock, a
    # loop), and then the batch waited forever. On the API that meant an SSE client pinned open and
    # the batch's cancel registry never popped, with nobody at a terminal to press Ctrl-C.
    #
    # Wall-clock for the WHOLE fan-out, not per task (see chimera.concurrency.run_all_with_deadline,
    # which argues the case): a bigger batch on fewer workers therefore gets less time per task.
    # That is deliberate — what this bounds is how long one request may hold the process, not each
    # task's fairness.
    #
    # 4h is roughly 8x the heaviest batch anyone has a reason to run (8 tasks / 4 workers / 3
    # attempts of single-digit minutes each ≈ 30 min) and far past what a human waits for. It does
    # NOT clear the arithmetic worst case — 3 attempts × ~10 calls × the 600s call deadline is ~5h
    # for ONE task — and that is the honest limit of this default: a run in which every single call
    # parks at the provider's deadline is a broken provider, not work worth waiting out. 0 disables
    # the bound entirely (the pre-2026-08 behaviour) for a deployment that disagrees.
    batch_timeout: float = Field(default=14400.0, validation_alias="CHIMERA_BATCH_TIMEOUT")

    # Arm the taint-adaptive tool narrowing on the API server (`chimera app` / `chimera serve`).
    # Once a run consumes untrusted content, DANGEROUS_WHEN_TAINTED tools need approval; the server
    # has no tool-level approver yet, so that resolves to a refusal with an explanatory result —
    # fail closed. Set CHIMERA_TAINT_NARROW=0 on a deployment that must keep acting autonomously
    # after reading the web (and accept that a laundered injection could steer those tools).
    taint_narrow: bool = Field(default=True, validation_alias="CHIMERA_TAINT_NARROW")

    # Let the chat build durable memory when the user explicitly asks ("remember that…"). Opt-in for
    # privacy: chatting should not silently persist unless you asked it to. Off = the prior behaviour
    # where the desktop chat never wrote memory. Only explicit requests are captured — never automatic
    # extraction, which would pollute the store.
    remember_from_chat: bool = Field(default=False, validation_alias="CHIMERA_CHAT_MEMORY")

    # Run the cron daemon inside `chimera app` (the desktop backend), so scheduled jobs fire while
    # the app is open — the whole point of a proactive assistant. Defaults ON: a "briefing at 7am"
    # should just work once you've scheduled it, without a separate `chimera serve --cron` terminal
    # running 24/7. Set CHIMERA_APP_CRON=0 (or `chimera app --no-cron`) for a purely reactive app.
    app_cron: bool = Field(default=True, validation_alias="CHIMERA_APP_CRON")

    # Auto-start the messaging adapters (Discord/Telegram) inside `chimera app` at boot, so the agent
    # can reach you on chat without a separate `chimera serve --discord` terminal. OFF by default: it
    # opens a network bot, so it's a deliberate opt-in. The desktop UI's Messaging toggle sets this
    # and also starts/stops the adapter live; only a configured platform (token present) starts.
    app_messaging: bool = Field(default=False, validation_alias="CHIMERA_APP_MESSAGING")

    # Opt-in OpenTelemetry: export OTLP spans (tool calls) + metrics (tokens/cost) so an autonomous
    # run is observable in Jaeger/Tempo/Grafana. Off by default and zero-overhead; needs the [otel]
    # extra. Also auto-enabled when the standard OTEL_EXPORTER_OTLP_ENDPOINT is set.
    otel: bool = Field(default=False, validation_alias="CHIMERA_OTEL")

    # Are the files in the workspace trusted? Default True: `chimera solve` usually runs on YOUR OWN
    # repo, and tainting every `read_file` would make `--taint` fire on every run (unusable). Set
    # False when running against code you do NOT control — a third-party repo, a PR branch, anything
    # downloaded — so a `read_file` of a poisoned source file taints the run like a fetched page does,
    # arming the same tool-narrowing gate. Only takes effect under `--taint`. (The sandbox is still the
    # real boundary for hostile code — see SECURITY.md.)
    trust_workspace: bool = Field(default=True, validation_alias="CHIMERA_TRUST_WORKSPACE")

    # Should the CHAT agent be assembled with the same protections the coding turn gets — a write
    # region, a posture denylist, and the taint ledger wrapped around every tool?
    #
    # Default False, and that default is a real exposure, chosen deliberately. The chat registry is
    # shared with the messaging gateway and the OpenAI-compatible endpoint, so turning this on by
    # default would silently take shell away from agents people already run in Discord. Off, the chat
    # keeps the tools it has always had — and keeps the hole they come with: ask it to read a web page
    # that carries a planted instruction, and nothing stops it from writing the file that instruction
    # names. The coding turn refuses, because its ledger marks the run tainted.
    #
    # Because the default is the permissive one, the app STATES it: the posture line says, in a chat
    # without a ledger, that this conversation can write after reading untrusted content. A silent
    # permissive default is the one version of this decision that cannot be defended.
    guard_chat: bool = Field(default=False, validation_alias="CHIMERA_GUARD_CHAT")

    # Base URL for a local Ollama server. A model like `ollama/llama3` runs on your machine with no
    # API key — set this only if Ollama listens somewhere other than the default. Reinforces the
    # fully-local, self-hostable path: `CHIMERA_MODEL=ollama/llama3` and no key needed.
    ollama_base_url: str = Field(
        default="http://localhost:11434", validation_alias="CHIMERA_OLLAMA_BASE_URL"
    )

    # Inline completion in the editor: the model asked what comes after the cursor, and the hard
    # cut on how long it may take.
    #
    # A **base** tag, not an instruct one, and that is not a preference. Fill-in-the-middle needs
    # the template that consumes `suffix`; an instruct model ignores it and answers in prose, so
    # the grey text becomes "Sure! Here is a function that...". The default names a small base
    # model; if it is not pulled the editor says so and names the pull command, because a feature
    # that is silently off is indistinguishable from one that is broken.
    complete_model: str = Field(
        default="qwen2.5-coder:1.5b-base", validation_alias="CHIMERA_COMPLETE_MODEL"
    )
    complete_budget_ms: int = Field(default=600, validation_alias="CHIMERA_COMPLETE_BUDGET_MS")

    # Aggregate dollar ceiling for ONE day, across everything that writes to the usage log. Unset
    # (the default) means no daily cap and therefore no new way for a scheduled job to be refused.
    #
    # Read from the log rather than a counter so it survives a restart, and refused LOUDLY: the job
    # gets `last_status="budget"` with the numbers, because a refusal that only looked like "nothing
    # happened" is indistinguishable from a dead daemon. A job marked `critical` is exempt — a
    # position guardian silenced at 2 p.m. until midnight costs more than it saves.
    daily_usd_cap: float | None = Field(default=None, validation_alias="CHIMERA_DAILY_USD_CAP")

    # Who says yes when governance escalates an action to review: `ask` | `deny` | `allow`.
    #
    # Both governance layers have taken an approver since they were written and never been given
    # one, which measured out as 100% of dangerous-class calls refused on any run that read
    # something external (bench/injection/PREREGISTRATION.md). The gate was never too strict —
    # there was nothing on the other side of it.
    #
    # `ask` degrades to `deny` with no terminal attached, which is what a cron job has. Degrading
    # the other way would make an unattended deployment the most permissive configuration in the
    # product, which is backwards. `allow` exists for a workspace whose contents the owner already
    # trusts, and every grant is recorded so the choice does not become invisible.
    #
    # ⚠ This read `CHIMERA_APPROVAL` — the SAME env var as `approval` above — from the day it was
    # written. Pydantic populated both fields from one variable, and the two vocabularies do not
    # overlap in a single value, so every documented setting was broken in one direction or the
    # other. Measured across all six: `ask`, `allow` and `deny` raised `ValidationError` out of
    # `deployment_posture` (so `ask`, the documented default HERE, killed every coding turn), while
    # `always`, `suspicious` and `never` arrived here unrecognised, fell through to the `ask` branch
    # and — headless, which is what cron is — refused everything. An owner writing "never stop and
    # ask" got the exact opposite, as a refusal string the agent reads past.
    #
    # They are not the same axis and never were. `approval` answers "when should a run pause for
    # me?" — a posture question, owned by the desktop Settings screen, which writes that variable.
    # This one answers "what happens when the approver is consulted?" — a policy question, read by
    # `solve` and by every unattended surface. So this one moves, because nothing writes it and
    # nothing documented it, while renaming the other would silently break saved app settings.
    approval_mode: str = Field(default="ask", validation_alias="CHIMERA_APPROVAL_MODE")

    # Governance on the unattended surfaces (`serve`, cron, MCP, A2A, messaging): `off` | `observe`
    # | `enforce`. Off by default, because turning it on changes what a running deployment is
    # allowed to do and nobody should get that from an upgrade.
    #
    # `observe` runs the entire stack and refuses nothing, recording every action enforcement WOULD
    # have refused. That middle state exists because the failure it guards against is silent: with
    # narrowing on and no approver, a job that reads a feed cannot write for the rest of its run,
    # the refusal arrives as an ordinary observation string, and the run reports success having done
    # nothing. Going straight to `enforce` on a schedule that watches real positions is how that
    # gets discovered in production instead of in a report.
    governance_mode: str = Field(default="off", validation_alias="CHIMERA_GOVERNANCE")

    # Deployment-level tool allowlist/denylist (names). Empty allowlist = no restriction (all
    # tools); a non-empty allowlist grants only those. Denylist removes even if allowed.
    #
    # These apply on every surface, and that sentence was false for three weeks before it was true:
    # `run`/`solve` in a terminal, the coding turn, the autonomous run, the parallel batch, the
    # desktop app's chat, and the unattended surfaces — `serve`, the cron dispatch, MCP, A2A, and the
    # messaging bots started either way.
    #
    # The last group used to be conditional on `CHIMERA_GOVERNANCE=observe|enforce`, which defaults
    # to `off`, so on a stock deployment a denylist written here fenced NEITHER Discord bot. That was
    # a filing error rather than a decision: these lists are an instruction (the tool is in the
    # registry or it is not), while the trust kernel and taint ledger are an inference that can
    # refuse legitimate work — only the second needs a rollout to be priced first, and only the
    # second is still staged behind that variable.
    #
    # Where a request carries its own allowlist the two INTERSECT — this list is a ceiling, and a
    # caller must not be able to raise it.
    # `NoDecode` is not decoration. Without it, pydantic-settings' EnvSettingsSource runs
    # `json.loads` on the raw string BEFORE the comma-splitting validator below ever sees it, so
    # `CHIMERA_TOOL_DENYLIST=run_shell` raises `SettingsError` at import of `get_settings()` — and
    # since every entry point builds settings first, the whole CLI stops opening. `chimera --help`
    # exits 1 on a machine whose only sin was fencing its agent the way `.env.example` says to.
    #
    # Ten list fields in this class carry the annotation and these two were the only ones without
    # it, which is why nothing looked odd. The tests missed it for a sharper reason: they build
    # `Settings(CHIMERA_TOOL_DENYLIST="...")` by keyword, and a keyword goes through
    # InitSettingsSource, which does no JSON decoding at all. Thirty-eight green tests exercised a
    # path no deployment uses.
    tool_allowlist: Annotated[list[str], NoDecode] = Field(
        default_factory=list, validation_alias="CHIMERA_TOOL_ALLOWLIST"
    )
    tool_denylist: Annotated[list[str], NoDecode] = Field(
        default_factory=list, validation_alias="CHIMERA_TOOL_DENYLIST"
    )

    @field_validator("approval", mode="before")
    @classmethod
    def _approval_is_a_posture_word(cls, value: object) -> object:
        """Keep the posture vocabulary out of the governance one, and say so when they are mixed.

        The two fields shared an env var, so a value from the wrong side used to fail deep and late:
        `CHIMERA_APPROVAL=ask` raised `ValidationError: 1 validation error for Posture` on every
        coding turn, which names neither the setting nor the fix. Warning at construction and
        falling back to "state nothing" is the same shape `governed_profile` already uses for an
        unknown mode — a typo must not silently enable or silently disable something stricter than
        intended, and "" is the value that changes nothing.
        """
        if not isinstance(value, str):
            return value
        word = value.strip().lower()
        if word in _GOVERNANCE_WORDS:
            _log.warning(
                "CHIMERA_APPROVAL=%r is the governance vocabulary; that setting is now "
                "CHIMERA_APPROVAL_MODE. Ignoring it here (no posture floor is stated).",
                word,
            )
            return ""
        if word and word not in _POSTURE_WORDS:
            _log.warning(
                "CHIMERA_APPROVAL=%r is not one of %s; ignoring it (no posture floor is stated).",
                word, ", ".join(sorted(_POSTURE_WORDS)),
            )
            return ""
        return word

    @field_validator("approval_mode", mode="before")
    @classmethod
    def _approval_mode_is_a_policy_word(cls, value: object) -> object:
        """The mirror. Unknown falls back to `ask`, which degrades to deny with no terminal —
        the fail-closed end, so a typo cannot widen what an unattended deployment may do."""
        if not isinstance(value, str):
            return value
        word = value.strip().lower()
        if not word:
            return "ask"
        if word in _POSTURE_WORDS:
            _log.warning(
                "CHIMERA_APPROVAL_MODE=%r is the posture vocabulary; that setting is "
                "CHIMERA_APPROVAL. Falling back to 'ask'.",
                word,
            )
            return "ask"
        if word not in _GOVERNANCE_WORDS:
            _log.warning("CHIMERA_APPROVAL_MODE=%r is not one of %s; falling back to 'ask'.",
                         word, ", ".join(sorted(_GOVERNANCE_WORDS)))
            return "ask"
        return word

    @field_validator(
        "fusion_panel",
        "fusion_panel_temperatures",
        "transfer_panel",
        "fallback_models",
        "openrouter_keys",
        "openai_keys",
        "anthropic_keys",
        "gemini_keys",
        "deepseek_keys",
        "tool_allowlist",
        "tool_denylist",
        mode="before",
    )
    @classmethod
    def _split_panel(cls, value: object) -> object:
        """Accept a comma-separated string from the environment — or a JSON array.

        Comma-separated is the documented form and what `.env.example` shows. JSON is accepted
        because these fields carry ``NoDecode``, which turns pydantic-settings' own decoding off, and
        without this branch a value someone wrote as ``["a", "b"]`` would be split on the comma into
        ``['["a"', '"b"]']`` — a *silent* wrong answer where the previous behaviour was a loud crash.
        For a tool denylist that trade is the wrong way round: a fence made of nonsense names denies
        nothing and says so nowhere.
        """
        if not isinstance(value, str):
            return value
        text = value.strip()
        if text.startswith("[") and text.endswith("]"):
            import json

            try:
                parsed = json.loads(text)
            except ValueError:
                pass  # not valid JSON after all — fall through to the comma split
            else:
                if isinstance(parsed, list):
                    return [str(item).strip() for item in parsed if str(item).strip()]
        return [item.strip() for item in text.split(",") if item.strip()]

    def tier_ladder(self) -> TierLadder:
        """The resolved weak/mid/top model ladder (explicit override > cost_mode)."""
        from chimera.providers.catalog import resolve_tiers

        return resolve_tiers(self)

    def credential_pool(self, provider: str) -> list[str]:
        """Only the explicit multi-key pool (``CHIMERA_<PROVIDER>_KEYS``), [] if unset.

        This is what the gateway rotates round-robin. A provider with just a single
        ``*_API_KEY`` returns [] here — its key is read from the environment as before.
        """
        pools = {
            "openrouter": self.openrouter_keys,
            "openai": self.openai_keys,
            "anthropic": self.anthropic_keys,
            "gemini": self.gemini_keys,
            "deepseek": self.deepseek_keys,
        }
        return list(pools.get(provider, []))

    def key_pool(self, provider: str) -> list[str]:
        """Usable keys for a provider: the pool if set, else the single key."""
        pool = self.credential_pool(provider)
        if pool:
            return pool
        single = {
            "openrouter": self.openrouter_api_key,
            "openai": self.openai_api_key,
            "anthropic": self.anthropic_api_key,
            "gemini": self.gemini_api_key,
            "deepseek": self.deepseek_api_key,
        }.get(provider)
        return [single] if single else []

    def configured_providers(self) -> list[str]:
        """Providers that currently have a key: the five first-class ones, then whatever else the
        environment reveals.

        The five come first, and both groups are ordered deterministically, because this list is not
        only displayed — ``catalog._reachable`` compares the first segment of a model slug against
        it, so an unstable order would make tier resolution unstable with it.

        The second group is what stops the product refusing to work for someone holding a valid
        Groq or Mistral key; see :mod:`chimera.providers.discovery` for why the test is a name
        pattern rather than a lookup in LiteLLM's provider list.
        """
        from chimera.providers.discovery import generic_providers

        names = ("openrouter", "openai", "anthropic", "gemini", "deepseek")
        first = [name for name in names if self.key_pool(name)]
        return first + [name for name in generic_providers() if name not in first]

    def has_any_key(self) -> bool:
        return bool(self.configured_providers())

    def credentials(self) -> dict[str, str | None]:
        """All known credential slots keyed by env-var name (value or None)."""
        return {
            "OPENROUTER_API_KEY": self.openrouter_api_key,
            "OPENAI_API_KEY": self.openai_api_key,
            "ANTHROPIC_API_KEY": self.anthropic_api_key,
            "GEMINI_API_KEY": self.gemini_api_key,
            "DEEPSEEK_API_KEY": self.deepseek_api_key,
            "TAVILY_API_KEY": self.tavily_api_key,
            "BRAVE_API_KEY": self.brave_api_key,
            "SERPAPI_API_KEY": self.serpapi_key,
            "X_BEARER_TOKEN": self.x_bearer_token,
            "STABILITY_API_KEY": self.stability_api_key,
            "ELEVENLABS_API_KEY": self.elevenlabs_api_key,
            "SPOTIFY_CLIENT_ID": self.spotify_client_id,
            "SPOTIFY_CLIENT_SECRET": self.spotify_client_secret,
        }


#: The environment variable NAMES this process inherited, upper-cased, captured at import.
#:
#: A real environment variable beats ``.env`` — that is pydantic-settings' precedence for every field
#: here, and :func:`_export_env_file_credentials` below deliberately mirrors it with ``setdefault``.
#: So a ``CHIMERA_*`` exported by whatever started this process (``docker run -e``, a systemd unit, a
#: shell) is not just the value in force: it is a value the Settings screen cannot change, because
#: ``patch_config`` writes ``.env`` and the variable wins again at the next launch. The save
#: confirms, the value sticks for the session (the patch also writes ``os.environ``), and it reverts
#: at restart with nothing having said so — which is the worst shape a setting can have.
#:
#: Captured in the module body, which is provably before the first write: nothing can call
#: ``patch_config`` without importing this module first. Diffing ``os.environ`` against ``.env`` at
#: read time was the other candidate and it is wrong — it reports nothing while the two agree, which
#: is exactly the moment before the user saves the change that will silently revert.
#:
#: Upper-cased because ``model_config`` declares ``case_sensitive=False``: a lower-case export is
#: honoured by pydantic, so matching it case-sensitively here would miss a real pin.
_STARTUP_ENV: frozenset[str] = frozenset(name.upper() for name in os.environ)


def pinned_by_environment(keys: Iterable[str]) -> list[str]:
    """Which of ``keys`` came from the environment rather than from ``.env``, sorted.

    Membership, not equality of values: a key present in the startup environment cannot be changed
    durably from the UI regardless of what it currently holds, so the honest test is "was this
    inherited", not "does it differ from the file today".
    """
    return sorted(key for key in keys if key.upper() in _STARTUP_ENV)


def _export_env_file_credentials() -> None:
    """Put provider keys that live only in the ``.env`` into the process environment.

    ``Settings`` is declared ``extra="ignore"``, so a key it has no field for — ``GROQ_API_KEY``, say
    — is read from the file and silently dropped: it becomes neither an attribute nor an environment
    variable, and LiteLLM, which reads the environment, never sees it. Since ``chimera init`` writes
    a ``.env`` and the docs point people at it, that gap would leave someone who followed our own
    instructions with a working key and a product that will not start.

    ``setdefault``, not assignment: a value already in the process environment wins over the file,
    which is the precedence pydantic-settings applies to every field it does know about.
    """
    from chimera.providers.discovery import env_file_credentials

    for name, value in env_file_credentials(Settings.model_config.get("env_file")).items():
        os.environ.setdefault(name, value)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the cached process-wide settings instance."""
    _export_env_file_credentials()
    return Settings()
