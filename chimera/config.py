"""Runtime configuration for Chimera.

Settings are read from environment variables and an optional ``.env`` file.
Nothing here requires a key at import time — the agent only needs credentials for
the providers it actually calls (see :mod:`chimera.providers.gateway`).
"""

from __future__ import annotations

import logging
import math
import os
import re
from collections.abc import Iterable
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

# stdlib logging rather than `chimera.telemetry.get_logger`: telemetry reads settings, so importing
# it here is a cycle. Same logger tree either way — this lands under `chimera.config` like the rest.
_log = logging.getLogger("chimera.config")

#: The two vocabularies that shared one env var until they were split. Named here rather than
#: inline so each validator can recognise the OTHER side and say which variable the value belongs
#: to — a message that only says "invalid" sends someone to the wrong file.
_POSTURE_WORDS = frozenset({"always", "suspicious", "never"})  # CHIMERA_APPROVAL
_GOVERNANCE_WORDS = frozenset({"ask", "allow", "deny"})  # CHIMERA_APPROVAL_MODE
#: One ref segment `git worktree add -b <prefix>/attempt-<hex>` accepts on every platform, and no
#: more: no slash (a second segment would make `chimera/x` and `chimera` collide as ref and
#: directory), no dot (`.lock`, `..`), no leading `-` (it would read as an option). Forty is ample.
BRANCH_PREFIX_SHAPE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,39}")

#: Names Windows reserves for devices, in any case. The shape above accepts them, and git on
#: Windows cannot make the directory `.git/refs/heads/aux/` — measured: `git worktree add -b
#: aux/attempt-9` answers "fatal: cannot lock ref ... unable to create directory", so every isolated
#: run would fail. Only the exact name: `con-x` and `auxx` are ordinary names and work. `com0` was
#: accepted by one Windows 11 machine and `lpt0` refused; both are in Microsoft's reserved list and
#: refusing a name costs the owner nothing, so both are refused. (The superscript-digit forms
#: Windows also reserves cannot pass the ASCII-only shape.)
_WINDOWS_DEVICE_NAMES = frozenset(
    {"con", "prn", "aux", "nul"}
    | {f"com{n}" for n in range(10)}
    | {f"lpt{n}" for n in range(10)}
)


def is_branch_prefix(word: str) -> bool:
    """Whether ``word`` is a prefix `git worktree add -b <word>/attempt-<hex>` accepts on every
    platform this runs on: one ref segment of the right shape, and not a Windows device name."""
    return bool(BRANCH_PREFIX_SHAPE.fullmatch(word)) and word.lower() not in _WINDOWS_DEVICE_NAMES

if TYPE_CHECKING:
    from chimera.providers.catalog import TierLadder

# Two of these three were WITHDRAWN by their providers and nobody noticed until the catalogue was
# audited on 2026-08-18 — so the default fusion panel, the feature whose entire premise is several
# independent models answering, was convening one model and two 404s. A default that names somebody
# else's product decays on their schedule; `tests/test_catalog_is_live.py` now checks these too.
_DEFAULT_PANEL = [
    "openrouter/anthropic/claude-opus-5",
    "openrouter/openai/gpt-5.5",
    # Was `gemini-3.1-pro-preview`. A `-preview` slug in a DEFAULT is a default that can be
    # withdrawn without notice, and there is no stable Gemini 3.x "pro" to move to — only the flash
    # line ships non-preview. Measured on the live index 2026-09-03: 2.000/12.000 -> 0.750/3.750 per
    # million, and the third-party agentic index goes 23 -> 50. Cheaper AND the better number, which
    # is unusual enough to say out loud.
    "openrouter/google/gemini-3.8-flash",
]
# The judge must not be a panelist. It shipped as `_DEFAULT_PANEL[0]` — the same slug, verbatim —
# which made the default fusion self-evaluating in the one place this project claims to have an
# independent signal rather than a self-report. Nothing guarded it; `validate_fusion_roles` below
# does now. DeepSeek-R1 is a fourth vendor (the panel is Anthropic/OpenAI/Google) and is reasoning-
# tuned, which is what judging asks for.
#
# Moved off R1 on 2026-09-03, on three measurements rather than a preference. Its context window is
# 64k — the tier it serves asks for 100k, so the judge could not read what the panel produced on a
# long turn. Its third-party agentic index is 3.1, the lowest of any candidate examined, tied with a
# 20B model in the weak tier. And on a trivial write-a-file probe it took 209s against 29s for the
# then-default and 51s for the top model that replaced it.
#
# The replacement keeps DeepSeek as the judge's vendor, so the independence argument above is
# unchanged: the panel is Anthropic/OpenAI/Google and the judge is neither a panelist nor a
# vendor-mate of one.
_DEFAULT_JUDGE = "openrouter/deepseek/deepseek-v4-flash-0731"
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

    @model_validator(mode="before")
    @classmethod
    def _empty_boolean_is_unset(cls, data: Any) -> Any:
        """`CHIMERA_GUARD_CHAT=` must not stop the app from starting.

        Writing `VAR=` is how a line gets turned off without being deleted, it is what `export VAR=`
        leaves behind, and this repository's own `.env` ships with `OPENROUTER_API_KEY=` empty. Every
        one of the twenty-six boolean settings raised on it — and the failure was total (no CLI, no
        API, no desktop sidecar) with a pydantic traceback naming a type instead of a sentence naming
        the line to fix.

        An empty value is the absence of a value, and the absence of a value is what a default is
        for. Dropping the key rather than coercing to `False` is the whole of the care here: `False`
        would silently switch off every setting that defaults ON, a security posture among them, for
        anybody who left a blank line in their `.env`.

        **Booleans only.** For a string `""` can be a real answer — an empty allowlist is not the
        same as no allowlist — so sweeping every empty value into "unset" would change what those
        mean in order to fix a type that has no empty case at all.
        """
        if not isinstance(data, dict):
            return data
        booleanos = {
            str(campo.validation_alias or nome).lower()
            for nome, campo in cls.model_fields.items()
            if campo.annotation is bool
        }
        return {
            chave: valor
            for chave, valor in data.items()
            if not (str(chave).lower() in booleanos and isinstance(valor, str) and not valor.strip())
        }

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
    spotify_client_secret: str | None = Field(
        default=None, validation_alias="SPOTIFY_CLIENT_SECRET"
    )

    # --- Default single model (Tier 1 / cheap tasks) ---
    #
    # A cheap model rather than a frontier one, and the reason is what a default IS: the model a
    # fresh install spends money on before anyone has made a decision. When it moved off GPT-5.5 to
    # DeepSeek the live OpenRouter list price was $0.25/$0.95 per 1M against $5.00/$30.00 — twenty
    # times cheaper in, thirty times cheaper out, for the questions a first conversation asks.
    #
    # gpt-6-luna since 2026-09-26, replacing deepseek-v4-flash-0731, on the partial SWE-bench django
    # bake-off in `bench/default-model-bakeoff`, graded by the official harness: 123/154 resolved
    # (80%) at US$ 0.0069 per resolved instance, against 92/150 (61%) at US$ 0.0158. Unpaired and
    # partial — the paired run is still completing — so the gap is read as large, not as exact. The
    # same model had already won `bench/review_reviewer` (#638) as the default reviewer.
    #
    # This is no longer the `mid` rung of the cost presets, which still hold deepseek-v4-flash: the
    # presets drive the tier ladder (roles, cascade, orchestration), this drives the plain agent
    # turn, and the bake-off measured only the latter. Which rung luna should occupy is a separate,
    # unmeasured decision. Its useful context was measured in `bench/useful_context` (≥ 256k, a
    # lower bound; `CatalogEntry.useful_k`), so compaction spends at most that, not the window.
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
        default="openrouter/openai/gpt-6-luna", validation_alias="CHIMERA_DEFAULT_MODEL"
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
    # --- Blind presentation (arXiv 2609.08016): the judge and the agreed-path synthesiser read the
    # panel as `Answer A / B / C` in a shuffled order instead of `Answer 1 (model <vendor slug>)` in
    # arrival order. The vendor name and the position are not evidence about an answer; the trace
    # keeps the permutation (`shown_order`) so every letter is still attributed to its model. On by
    # default: `bench/judge_blind` measured the cost at zero (2026-09-11) on a corpus the judge could
    # solve alone. Set to 0 to show the judge vendor names and arrival order again. ---
    fusion_blind_panel: bool = Field(default=True, validation_alias="CHIMERA_FUSION_BLIND_PANEL")
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

    # --- Completion ceiling: the most tokens one call may generate when the caller set no
    # `max_tokens`. Without it the provider's own ceiling applies, and the reasoning model behind
    # the default tier can spend all of it thinking and return an empty `content` at 200 OK —
    # measured three times on 2026-09-11/12: the spec-test generator reasoned to **131,072** tokens
    # and returned nothing (`bench/spec_test_vacuity`, about 25x the cost of a normal call); the
    # AIME writers emptied 13 of 21 slots on the hard problems (`bench/judge_blind_hard`); the
    # fusion judge ran for 52 minutes on one SimpleQA item (`bench/judge_blind_qa`, where 8.5% of
    # runs carried 71% of the cost). The runaway is a property of the draw, not the task. 32k is
    # four times the longest converged reply seen (15k) and a quarter of the ceiling; 0 restores the
    # provider's. A caller that passes its own `max_tokens` is never touched. ---
    completion_ceiling: int = Field(default=32_000, validation_alias="CHIMERA_COMPLETION_CEILING")

    # --- Exact-match completion cache for tool-free turns (HORIZON prompt caching) ---
    cache: bool = Field(default=False, validation_alias="CHIMERA_CACHE")
    prompt_cache: bool = Field(default=False, validation_alias="CHIMERA_PROMPT_CACHE")
    """Opt-in: mark the stable system prefix with a provider cache breakpoint so the
    single agent / worker fleet reuse it at the cache read rate. Providers that cache
    automatically (OpenAI, DeepSeek) are left untouched; only breakpoint-requiring
    families (Anthropic/Claude) get an explicit cache_control marker."""

    # --- Browser tool: run Chromium headless (default) or headful for debugging. ---
    browser_headless: bool = Field(default=True, validation_alias="CHIMERA_BROWSER_HEADLESS")
    # There is no setting for the browser's viewport-first listing (study 24, M7). It lost on both
    # measurements, so a user could only switch it on to make the browser worse; the mode survives as
    # a constructor argument that `bench/browser_viewport_tasks` uses (see `BrowserTool.__init__`).
    #
    # The browser situation module (study 25, S11; `chimera/tools/browser_situation.py`). On, a
    # session that holds the browser gets its situation rules in the system prompt, a page that needs
    # the person (a sign-in, a two-step code, a captcha, a payment step) ends the run as `handover`,
    # and reading cookies, site storage or saved passwords through the tool is refused. OFF by
    # default: the rules' benefit is unmeasured, and the stop changes what a run does on a page with a
    # login form. Measured so far: no harm on 24 browsing tasks (`bench/browser_situation`: 44/48 on,
    # 45/48 off, no stop; still valid for walls v2 by a US$ 0 replay). Walls v2 on live pages
    # (`bench/browser_element_list/RESULTS-walls-v2.md`): no false stop on 27 ordinary pages, 6/6
    # walls on the in-sample set and 9/15 on a fresh one — not fit, so still off. Its named gaps: a
    # widget drawn just after `load`, and block pages that carry no challenge marker.
    browser_situation: bool = Field(default=False, validation_alias="CHIMERA_BROWSER_SITUATION")
    # Where the browser may go (study 29, P5.2; `chimera/tools/browser_reach.py`). Both EMPTY by
    # default, and empty is exactly the browser that shipped before them: any public site, no loopback.
    # `CHIMERA_BROWSER_SITES` (hosts and `*.domain`, comma-separated) only narrows: a top-level page
    # off the list asks a person on the Code screen and in the app's chat (the card both already
    # draw), and is refused where nobody can be asked: the CLI, the TUI, the bots, an unguarded chat.
    # `CHIMERA_BROWSER_LOCAL_PORTS` widens by one thing: localhost / 127.0.0.1 / [::1] on a port
    # listed here, so the agent can look at the dev server it is changing — never a port Chimera
    # itself serves on (the app's API answers approvals), never a private network or metadata.
    # Unmeasured: the plan asks for a small pre-registered bench ("verify a UI change on a local app")
    # before the local ports are suggested to anyone. It is not written or run (it needs paid calls),
    # so both stay off and the Settings rows say what each one does, not that it helps.
    browser_sites: str = Field(default="", validation_alias="CHIMERA_BROWSER_SITES")
    browser_local_ports: str = Field(default="", validation_alias="CHIMERA_BROWSER_LOCAL_PORTS")

    # --- The desktop bridge: may an MCP client (Claude Code / Claude Desktop, through
    # `chimera mcp desktop`) operate this running app? Both OFF by default, and the second means
    # nothing without the first (`chimera/api/desktop_bridge.py`). The first lets the client do what
    # the screens do — read and start conversations, runs, boards — under the owner's configured
    # posture. The second adds what the screens reserve for the person: answering approvals, and
    # editing settings. Credentials stay out of reach with both on.
    desktop_bridge: bool = Field(default=False, validation_alias="CHIMERA_DESKTOP_BRIDGE")
    desktop_bridge_full: bool = Field(default=False, validation_alias="CHIMERA_DESKTOP_BRIDGE_FULL")

    # --- Image generation backend: 'auto' (hosted if an OpenAI key is set, else local diffusers),
    # 'hosted' (OpenAI), or 'local' (run FLUX/SD via the imagegen-local extra — heavy, GPU). ---
    image_backend: str = Field(default="auto", validation_alias="CHIMERA_IMAGE_BACKEND")
    image_model_local: str = Field(
        default="black-forest-labs/FLUX.1-schnell",  # Apache-2.0 weights — commercially safe
        validation_alias="CHIMERA_IMAGE_MODEL_LOCAL",
    )

    # --- Long-term memory backend: sqlite (default since 0.59.0: FTS5 full-text, 0.2 ms a search at
    # any size) or json (one file, re-tokenized on every query: 70 ms a search at 4,000 facts —
    # bench/memory_recall). Resolved by `chimera.memory.backend`: the default falls back to json
    # on a Python without FTS5, and an existing memory.json is imported once into the new store. ---
    memory_backend: str = Field(default="sqlite", validation_alias="CHIMERA_MEMORY_BACKEND")

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
    # Reach MCP tools on demand (mcp_list / mcp_describe / mcp_call) instead of declaring every
    # server's full schema on every step of every turn. The comparable product that measured this
    # reported -46.9% of total agent tokens.
    #
    # OFF by default, and the reason is the half nobody measured: the saving is in tokens and the
    # risk is in selection accuracy, because a model that must search for a tool may choose worse
    # than one handed the list. `chimera.integrations.mcp_defer.describe_saving` reports the first
    # half on your own servers; until the second half is measured here, this stays a choice.
    mcp_defer: bool = Field(default=False, validation_alias="CHIMERA_MCP_DEFER")

    # --- The same shape for the BUILT-IN tools, which are the larger half of the bill.
    #
    # Twenty-two schemas go out on every step: ~3,205 tokens before the user types. Measured on 28
    # sessions of an installed 0.48.0 (33 tool calls), four tools did all of it — read_file,
    # write_file, list_dir, edit_file — and the eighteen never called were 86% of the schema.
    #
    # `chimera.tools.defer.CORE` is declared in full regardless, so the tools that observed use
    # actually reaches are never behind a lookup, and the saving comes from schemas the model did not
    # ask for. OFF by default for the reason above it: selection accuracy is still the unmeasured
    # half, and this project has rules against turning something on by conviction.
    # `chimera.tools.defer.describe_saving` reports the measured half on your own registry — and can
    # report a LOSS, which below a handful of tools it truthfully is.
    defer_tools: bool = Field(default=False, validation_alias="CHIMERA_DEFER_TOOLS")
    #: A project's `.chimera/pack.json` narrowing the skills, MCP servers and tools a run in that
    #: folder receives (study 29, P7.6; `chimera/core/project_pack.py`). OFF until measured:
    #: narrowing changes what a run can do, and `bench/project_pack/PREREGISTRATION.md` registers
    #: the comparison (prompt tokens and tool-selection accuracy, with and without) without having
    #: run it. Even on, a pack applies only after the owner accepted that file for that folder, and
    #: it can only remove — never install, activate, launch or allow anything.
    project_pack: bool = Field(default=False, validation_alias="CHIMERA_PROJECT_PACK")

    # --- The task list the agent keeps for itself (chimera/tools/todo.py).
    # On, and the reasoning differs from the two flags above it, so it is written down rather than
    # assumed. `defer_tools` and `edit_batch` are interventions with a quality claim: each has a
    # measurable arm, so each waits for its measurement. This one makes no quality claim. It records
    # what the agent says about its own progress, so the list survives a compaction (`RunState.tasks`
    # was built for exactly this and never received anything) and a person watching a long run can
    # see where it is. There is no arm that could refute it — it is either called or it is not.
    #
    # The price is stated instead: 657 characters of schema in every prompt of every step, which
    # `chimera.tools.todo.schema_cost_chars` recomputes rather than trusting this comment. What to
    # watch is adoption: a tool the model never calls is 657 characters of nothing, and that is the
    # number that would turn this default off.
    todo_list: bool = Field(default=True, validation_alias="CHIMERA_TODO_LIST")
    # --- The `decide` tool (study 22, phase 4): typed questions the agent asks over text it has,
    # answered by the configured decision backend. OFF by default for the reason `edit_batch` is: a
    # schema in every prompt of every step. Nothing is gated on its answers — they go to the agent.
    decide_tool: bool = Field(default=False, validation_alias="CHIMERA_DECIDE_TOOL")
    # --- `create_document` (study 29, P6.2): Word/Excel/PowerPoint/PDF from a declarative spec. OFF
    # for the reason `decide_tool` is — a rarely-used tool whose schema is paid on every step — and
    # not for any reach it adds: it writes only where `write_file` may, through the same gate.
    create_document: bool = Field(default=False, validation_alias="CHIMERA_CREATE_DOCUMENT")
    # --- The explorer's contract (study 25, S12; chimera/core/explorer.py). On, the explorer is
    # asked for a thoroughness level, reports findings with a path:line each and a gaps section, and
    # its cited locations are checked against the workspace. OFF by default: today's explorer text
    # stays byte for byte, and the new one has no measurement of its own yet.
    explorer_contract: bool = Field(default=False, validation_alias="CHIMERA_EXPLORER_CONTRACT")
    # --- The web research sub-agent (study 25, S12; chimera/core/research.py). On, the Code screen
    # and `chimera solve` gain a `research_web` tool: a sub-agent with read-only web tools whose
    # answer carries a receipt saying which cited URLs it actually saw. OFF by default: it is a new
    # tool schema in every prompt and a new model bill per call, earned only by its bench
    # (bench/web_research).
    research_agent: bool = Field(default=False, validation_alias="CHIMERA_RESEARCH_AGENT")
    # --- `open_pull_request` (study 29, P8.1; chimera/tools/pull_request.py). On, every surface that
    # builds the default registry — the Code screen, chat, cron, the bots — gains a tool that pushes
    # the workspace's current branch to `origin` and opens a pull request with the GitHub CLI. OFF by
    # default: it publishes the owner's code and text to a remote, which is a new place for their
    # data to go. On or off, EVERY call is a question to the owner — no approval mode, posture,
    # governance mode or remembered answer turns it into a yes (`approval.always_ask`). Owner-only:
    # the desktop bridge may not write it (`bridge_routes.OWNER_ONLY_SETTINGS`).
    pull_requests: bool = Field(default=False, validation_alias="CHIMERA_PULL_REQUESTS")
    # --- Where an approval question goes when there is nobody at a console.
    #
    # This is what makes the three-state gate reachable on the surfaces that need it most. A cron
    # job, a messaging bot and the Kanban board all run unattended, and all three ALREADY asked for
    # the durable approval path — `home=settings.home`, with a comment at one of them reading
    # "Governance on the path that runs unattended". The parameter was dropped one layer down and
    # never reached the approver, so every REVIEW on those surfaces was refused without anybody
    # being asked, and the only trace was a line in `audit.jsonl`.
    #
    # Forwarding it alone would not have been a fix. `pending.ask_durably` waits fifteen minutes,
    # and no call site ever passed a `deliver` — so the question would have been a JSON file in
    # `<home>/approvals/` that nobody was told about, which `pending.py` itself names as the thing
    # to avoid: "pretending otherwise would park a worker for fifteen minutes to reach the same
    # refusal." So the durable ask is used when, and only when, this says where to send it.
    #
    # A webhook URL rather than a bot token, for the reason `scheduler/delivery.py` gives about job
    # results: a URL is copied out of a channel's settings, while a bot needs an application, a
    # token, an invite and a server to administer. **The URL is a credential** — whoever holds it
    # can post into that channel — so it is never logged in full.
    approval_webhook: str = Field(default="", validation_alias="CHIMERA_APPROVAL_WEBHOOK")
    # --- Answer that question from the chat bot, with a one-time code (study 29, P3.3).
    #
    # Off, an approval delivered to the webhook is answered only with `chimera approve` in a
    # terminal — which is exactly what the owner's phone does not have. On, each question delivered
    # through `approval_webhook` carries a fresh six-digit code, and the bot accepts
    # `aprovar <id> <code>` / `recusar <id> <code>` from an id in THAT platform's allowlist, before
    # the message can become a turn (`chimera/server/chat_approval.py`).
    #
    # Off by default because approving from a chat opens a spoofing surface where there was none,
    # and approvals are the owner's. Refused outright — with a warning at startup — for a platform
    # whose allowlist is empty: "anyone who can message the bot" may not approve anything. Not
    # editable from the app (`config_api`), on purpose: the desktop bridge can drive the settings
    # API, and the switch that lets a chat approve must not be one a model can flip.
    approve_via_chat: bool = Field(default=False, validation_alias="CHIMERA_APPROVE_VIA_CHAT")

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
    skill_cards_min_overlap: int = Field(
        default=2, validation_alias="CHIMERA_SKILL_CARDS_MIN_OVERLAP"
    )
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
    #: Mint learned skills even when the agent cannot read them back. OFF, and that is the point.
    #:
    #: Reading is opt-in because it was MEASURED and did not clear its gate (`bench/skillcard`:
    #: +16.7pp, CI includes zero, +300% tokens). Minting was never coupled to it, so the default
    #: install paid for a proposal call, a validation and a smoke test — and on the panel path a
    #: proposal per panel model plus a nine-model transfer probe — to produce cards nothing in the
    #: loop would ever read. Four projects run end to end minted 14 of them and used 0.
    #:
    #: A person can still browse them on the Knowledge screen, which is why this exists at all
    #: rather than the write being deleted: set it to keep collecting a library on purpose.
    mint_unreadable_skills: bool = Field(
        default=False, validation_alias="CHIMERA_MINT_UNREADABLE_SKILLS"
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

    # --- The threshold the comment above tells you to lower, and could not.
    #
    # It was a constructor default of 0.5 inside `AutoSkillEvolver` that `build_evolution_context`
    # never passed, so it was unreachable by configuration — while BOTH the comment above and a
    # runtime log line ("Lower min_transfer, enlarge the panel, or use 'point'") advised changing
    # it. Advice pointing at a knob that does not exist is worse than no advice: it sends someone
    # looking for a setting, and they find nothing and assume the fault is theirs. ---
    skill_min_transfer: float = Field(
        default=0.5, validation_alias="CHIMERA_SKILL_MIN_TRANSFER", ge=0.0, le=1.0
    )

    # --- SkillCoach process filter for `chimera evolve export`: keep only trajectories
    # whose step-following score >= this (so a lucky success with failed tool steps is not
    # trained on). 0.0 = off (default). ---
    sft_min_process: float = Field(default=0.0, validation_alias="CHIMERA_SFT_MIN_PROCESS")

    # --- Compact tool schemas at advertise-time (Improvement #5a): strip annotation
    # noise and trim parameter prose from the `tools=` payload re-sent every ReAct step.
    # Semantics preserved (name/type/required/enum kept). Off by default; the win is
    # largest with verbose MCP/OpenAPI toolsets — measure with `chimera schema-bench`. ---
    compact_schemas: bool = Field(default=False, validation_alias="CHIMERA_COMPACT_SCHEMAS")

    # Two measurement instruments, both off by default, both from `bench/cache_confound`.
    #
    # `CHIMERA_TEMPERATURE` overrides the WORKER LOOP's sampling temperature (default 0.2 in
    # `AgentConfig`) and nothing else: the planner (`Planner.plan`, 0.2), the manager and the fusion
    # roles keep their own. Set to 0 to make the loop reproducible enough that a change in trajectory
    # can be attributed to something other than sampling — which is the only way to ask whether the
    # provider's prefix cache moves a trajectory (arXiv 2609.04748 measured 36.2% of episodes on
    # self-hosted stacks). Measured 2026-09-15: a run that wants a byte-identical first request must
    # also pass `--no-plan`, or four T=0 workers start from four T=0.2 plans and diverge at step 1
    # for the ordinary reason (`bench/cache_confound`, Amendment 2).
    agent_temperature: float | None = Field(default=None, validation_alias="CHIMERA_TEMPERATURE")

    # `CHIMERA_PREFIX_NONCE` prepends a line to the system prompt so the prefix is unique to the run.
    # It was meant as the analogue of the paper's "cache off" on a route with no off switch, and it
    # is not one — measured 2026-09-15 (`bench/cache_confound`): it defeats the share of the system
    # prefix ACROSS runs (step-1 `cached_tokens` 0 where a shared run reads ~5,400 of ~6,200 from
    # cache) and nothing else, because a multi-step agent re-sends its own transcript and the
    # provider serves that intra-run prefix from cache whatever the system prompt says; the run-level
    # hit rate barely moves (0.83 against 0.84–0.89). Kept as the instrument it is: a cross-run-share
    # switch, verifiable on the receipt (`cache_read_tokens` lands on every attempt). Never set in
    # production: it throws away the ~10x cheaper cached prompt tokens on purpose.
    prefix_nonce: str = Field(default="", validation_alias="CHIMERA_PREFIX_NONCE")

    # `CHIMERA_PROVIDER_ORDER` pins an OpenRouter request to named providers, in order, with
    # fallbacks OFF. Comma-separated route names as OpenRouter spells them. Off by default. The
    # reason it exists is a measurement: a score belongs to the route that served it, and the same
    # model id served by two routes is two instruments (`bench/context_rot` found the two disagreeing
    # 7/10 against 2/15). With fallbacks on, an arm silently becomes whatever answered — the confound
    # wearing the manipulation's name. OpenRouter only: other providers may reject the field.
    provider_order: str = Field(default="", validation_alias="CHIMERA_PROVIDER_ORDER")

    # What an OpenRouter route may do with the prompt (study 29, P5.6). OpenRouter forwards a request
    # to whichever upstream provider serves the model, and some of those providers keep prompts or
    # train on them; nothing in this project could ask for otherwise, so the VPS's default route sent
    # every turn, memory recall included, under whatever the cheapest route's policy happened to be.
    #
    # `CHIMERA_OPENROUTER_DATA_COLLECTION=deny` sends `provider.data_collection: "deny"` (only routes
    # that do not store or train on the data may serve it); `CHIMERA_OPENROUTER_ZDR=true` sends
    # `provider.zdr: true` (only zero-data-retention endpoints). Both ship OFF — `allow`/false send
    # NOTHING, so a default request is byte-identical to the one before this setting existed — because
    # each one narrows the routes that may answer: a model whose every route keeps data stops being
    # reachable, and what is left may be slower. The plan's measurement (for the mandate's models,
    # how many lose every route and the latency of what remains) has not been taken; until it is,
    # turning these on is the owner's trade to make, not a default.
    #
    # Only `openrouter/` routes carry them; other providers do not know the field. The Decisions API
    # backend (`chimera/decisions/openrouter.py`) is a separate endpoint and does not send them — the
    # privacy card on the Security screen says so when that backend is the one configured.
    openrouter_data_collection: Literal["allow", "deny"] = Field(
        default="allow", validation_alias="CHIMERA_OPENROUTER_DATA_COLLECTION"
    )
    openrouter_zdr: bool = Field(default=False, validation_alias="CHIMERA_OPENROUTER_ZDR")

    # `CHIMERA_KEY_VAULT=true` makes the desktop Settings screen save a credential into the operating
    # system's vault (`chimera/config_vault.py`) instead of into `.env`, leaving a comment in the
    # file where the key was (study 29, P7.7). The vault itself is older than this switch — `chimera
    # secrets set` and the startup read in `get_settings` — and this only decides where the SCREEN
    # writes. OFF by default: it changes where a key lives, it depends on an optional extra the
    # frozen build may not carry, and a keychain that later refuses (locked, a prompt cancelled, a
    # re-signed binary on macOS) is a key the app cannot read. With no vault on the machine the save
    # falls back to `.env` and says so. Only the owner may switch it: the bridge refuses the key
    # (`bridge_routes.OWNER_ONLY_SETTINGS`), because its other direction puts keys back in the file.
    key_vault: bool = Field(default=False, validation_alias="CHIMERA_KEY_VAULT")

    # Whether the agent's READ tools may read Chimera's own `.env` - the file the Settings screen
    # saves provider keys into (owner's decision, 2026-10-04). ON by default, which is how it always
    # was: an owner who asks the agent to look at the install folder gets the file. When on, the
    # keys in it can reach the model and its provider; off, read_file, grep, glob, list_dir, the
    # document reader and the explorer refuse or hide that one file (`chimera/tools/workspace.py`),
    # recognised by identity so no other spelling of it gets through. Other projects' `.env` files
    # are untouched. Owner-only: the bridge refuses it (`bridge_routes.PRIVACY_SETTINGS`), since
    # turning it back on loosens privacy. Read per tool call, so it applies from the next one.
    agent_reads_own_env: bool = Field(default=True, validation_alias="CHIMERA_AGENT_READS_OWN_ENV")

    # `CHIMERA_REVIEW_MODEL` names the model `chimera review` reviews with. Empty (the default) lets
    # the command pick the first model measured as a reviewer whose family differs from the
    # author's (`MEASURED_REVIEWERS` in `chimera/review/family.py`, chosen by `bench/review_reviewer`),
    # then the tier ladder's, because a reviewer from the author's own family shares its blind spots
    # and prefers its output (study 25 §2.9). A value here is honoured even when it is the author's
    # family, and the report says so rather than overriding the choice.
    review_model: str = Field(default="", validation_alias="CHIMERA_REVIEW_MODEL")

    # --- Messaging bot tokens (only needed for the matching `chimera serve --<platform>`) ---
    discord_bot_token: str | None = Field(
        default=None, validation_alias="CHIMERA_DISCORD_BOT_TOKEN"
    )
    telegram_bot_token: str | None = Field(
        default=None, validation_alias="CHIMERA_TELEGRAM_BOT_TOKEN"
    )
    slack_bot_token: str | None = Field(default=None, validation_alias="CHIMERA_SLACK_BOT_TOKEN")
    slack_app_token: str | None = Field(default=None, validation_alias="CHIMERA_SLACK_APP_TOKEN")
    whatsapp_access_token: str | None = Field(
        default=None, validation_alias="CHIMERA_WHATSAPP_ACCESS_TOKEN"
    )
    whatsapp_phone_number_id: str | None = Field(
        default=None, validation_alias="CHIMERA_WHATSAPP_PHONE_NUMBER_ID"
    )
    whatsapp_verify_token: str | None = Field(
        default=None, validation_alias="CHIMERA_WHATSAPP_VERIFY_TOKEN"
    )
    whatsapp_app_secret: str | None = Field(
        default=None, validation_alias="CHIMERA_WHATSAPP_APP_SECRET"
    )  # set to verify the inbound webhook's X-Hub-Signature-256 HMAC
    # Who may talk to each bot — comma-separated platform ids (Discord/Telegram/Slack user ids, a
    # Signal number or uuid, a WhatsApp number). Empty = anyone, which is what every bot did before
    # these existed: the adapters have taken an allowlist since they shipped and no construction
    # path ever filled it, so a bot answered whoever reached it, with the owner's tools and the
    # owner's spend. Empty stays "anyone" rather than "nobody" because a bot already deployed on
    # that default would go silent on upgrade (the author's own ran on it until 0.64.2 set an
    # allowlist); `chimera serve` and the Settings card say so loudly instead. Read when a bot is built, so a change
    # applies at the next launch.
    discord_allowed_users: Annotated[list[str], NoDecode] = Field(
        default_factory=list, validation_alias="CHIMERA_DISCORD_ALLOWED_USERS"
    )
    telegram_allowed_users: Annotated[list[str], NoDecode] = Field(
        default_factory=list, validation_alias="CHIMERA_TELEGRAM_ALLOWED_USERS"
    )
    slack_allowed_users: Annotated[list[str], NoDecode] = Field(
        default_factory=list, validation_alias="CHIMERA_SLACK_ALLOWED_USERS"
    )
    signal_allowed_users: Annotated[list[str], NoDecode] = Field(
        default_factory=list, validation_alias="CHIMERA_SIGNAL_ALLOWED_USERS"
    )
    whatsapp_allowed_numbers: Annotated[list[str], NoDecode] = Field(
        default_factory=list, validation_alias="CHIMERA_WHATSAPP_ALLOWED_NUMBERS"
    )
    # Study 29, P6.3: the Discord bot attaches the deliverables its turn wrote (a PDF, a sheet, a
    # chart) to its reply. OFF, and refused while CHIMERA_DISCORD_ALLOWED_USERS is empty even when
    # on: a file in a channel is the owner's data where others read it, and an open bot would make
    # the owner's tools a file-export service for anyone (`chimera/server/attachments.py`). Read
    # when the bot is built, so a change applies at the next launch.
    discord_attach_files: bool = Field(default=False, validation_alias="CHIMERA_DISCORD_ATTACH_FILES")
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

    # --- Execution sandbox for the shell tool ---
    # `auto` (default): the platform's kernel sandbox where there is one — Seatbelt on macOS,
    # bubblewrap on Linux — and the host with a warning where there is not (Windows, and any Linux
    # whose kernel refuses the user namespace). `local` is the explicit opt-out to the host, `os`
    # forces the attempt, `docker` is the isolated container.
    #
    # This default used to be `local`. That meant the shipped boundary was a confirmation prompt,
    # which is a boundary a person can wave through and an injected instruction cannot be stopped by.
    sandbox: str = Field(default="auto", validation_alias="CHIMERA_SANDBOX")
    sandbox_image: str = Field(default="python:3.12-slim", validation_alias="CHIMERA_SANDBOX_IMAGE")
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
    # Should the VERIFY command (tests, a build) get the network? Off by default: the verifier runs
    # where the agent's shell runs, and that sandbox has no network. When on, a docker sandbox is
    # given the bridge for the verify step only. The kernel sandboxes cannot be asked for one
    # (bubblewrap unshares the network, Seatbelt denies it by default), so there a verifier that
    # needs the network runs on the host, and only when you typed it — a command inferred from the
    # repository, read from a cron job, a card or a workflow abstains instead.
    verify_network: bool = Field(default=False, validation_alias="CHIMERA_VERIFY_NETWORK")
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
    # Background shell jobs (`run_shell` with `background: true`, `chimera/core/jobs.py`): how many
    # may run at once, and how long one may run before it is killed with everything it started and
    # recorded as `timed_out`. UNSET, both are advice (3 at once, 6 hours) said in the start message
    # and never enforced: the owner decided on 2026-09-27 that a refused or killed job reads as the
    # agent giving up on something it was handed on purpose. SET, each is a hard limit, because a job
    # outlives the turn that started it and an owner who wants a bound can name one. A value below 1
    # means the default, not "off": turning the tool off is `CHIMERA_TOOL_DENYLIST=run_shell`.
    jobs_max_running: int | None = Field(default=None, validation_alias="CHIMERA_JOBS_MAX_RUNNING")
    jobs_max_runtime: int | None = Field(default=None, validation_alias="CHIMERA_JOBS_MAX_RUNTIME")

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

    # What that narrowing keys on. `provenance` (default): ANY external read arms it, the user's own
    # included — measured 2026-09-08 (`bench/injection/RESULTS.md`) as a 100% false-positive rate on
    # legitimate user-requested tool flows, byte-identical to the attack, because `record_fetch` set
    # `tainted=True` with no record of who asked. `authority`: a fetch whose URL or path the user
    # named in the task no longer arms the coarse narrowing; the per-action flow rules, the
    # query-string rule and the durable provenance bit are untouched.
    #
    # OFF by default, and it stays off: the same results file measures what the mode lets through
    # when the user asked to summarise the poisoned page, and that number is the reason. Reaches the
    # ledger through its construction sites (`TaintLedger(authority=settings.taint_authority)`); the
    # label itself (`CapabilityEvent.requested_by`) is recorded in every mode.
    taint_authority: Literal["provenance", "authority"] = Field(
        default="provenance", validation_alias="CHIMERA_TAINT_AUTHORITY"
    )

    # Destinations for which a query-string GET is NOT treated as a way out, while the run holds
    # untrusted content. Comma-separated hosts, empty by default — nothing is exempt until someone
    # says so.
    #
    # The rule it narrows is measured and worth keeping: `bench/injection` put `asr_exfil` at 0.500
    # before it and 0.000 after. Its price is measured too — over-block 0.500 -> 0.625, and five
    # questions per session of external-read work — and the results file says in as many words that
    # whether five is acceptable "is a product decision this table informs and does not make". This
    # is the knob for answering it without answering "no gate at all".
    #
    # ⚠️ Two things it is NOT. It is not a claim that the host is harmless: it is a claim that a GET
    # to it does not hand data to a third party. `api.github.com` qualifies because reading a repo
    # tree returns data to the caller and leaves nothing an attacker can read back; a host that
    # renders what you send it, or whose logs the attacker can read, does not — and a host that
    # accepts POSTs is irrelevant here, because this narrows GETs only and `http_post` is gated
    # elsewhere. And it is not validated by the bench: the corpus's attack host (`evil.test`) and its
    # legitimate hosts are disjoint by construction, so no measurement can tell you that YOUR list is
    # safe. Choosing it is your act, and it is the reason this defaults to empty.
    egress_allow: str = Field(default="", validation_alias="CHIMERA_EGRESS_ALLOW")

    # Study 30, S30-27. The rule above reads the query string only, and only once the run is
    # tainted: `https://SECRET.attacker.test/` and `https://attacker.test/SECRET/` went out, and in a
    # run where the USER pasted the injection (arXiv 2610.01768) the query went out too. On, a fetch
    # whose host labels, path segments or query carry a data-like value (16+ characters, mixed,
    # high-entropy) that appears neither in the instruction nor in anything the run fetched is a
    # question. OFF until measured: `bench/exfil_url/RESULTS.md` has the attack rate per channel and
    # the false-question rate per class of benign URL, including the class it cannot help asking
    # about (a commit hash the agent read from `git log`). Hosts in CHIMERA_EGRESS_ALLOW are exempt.
    # Only the public web's fetch tools make a value "seen": a key in an email, a calendar entry or a
    # connector's output is asked about. NOT covered: a fetch tool is what it judges, so the shell
    # (`curl`, `wget`, `dig`, `python -c` in run_shell) sends a value out unasked — see RESULTS.md.
    exfil_host_path: bool = Field(default=False, validation_alias="CHIMERA_EXFIL_HOST_PATH")

    # Study 30, S30-28. `pip install <name>` is a question; `git clone <owner/repo the model
    # guessed>` was not, and `run_shell` is not a fetch tool, so a cloned README or a page `curl`
    # printed left the run clean (arXiv 2607.07433: 92.4% of owners hallucinated for recent
    # repositories). On: a clone of a remote the user's instruction never named is a question; a
    # shell `git clone` / `curl URL` / `wget URL` is recorded as a fetch, so its output taints the
    # run like `http_get`'s; and a `pip install` card says what PyPI knows of each package (exists
    # since when / does not exist / unknown offline — display only). OFF until measured:
    # `bench/shell_fetch/RESULTS.md`.
    shell_fetch_guard: bool = Field(default=False, validation_alias="CHIMERA_SHELL_FETCH_GUARD")

    # Let the chat build durable memory when the user explicitly asks ("remember that…"). Opt-in for
    # privacy: chatting should not silently persist unless you asked it to. Off = the prior behaviour
    # where the desktop chat never wrote memory. Only explicit requests are captured — never automatic
    # extraction, which would pollute the store.
    remember_from_chat: bool = Field(default=False, validation_alias="CHIMERA_CHAT_MEMORY")
    # Study 25 S13: after a chat or Code turn, one model call proposes facts the user STATED about
    # themselves, and the harness keeps only those it can trace to the user's own words
    # (`chimera.memory.extract`). The same switch quotes recalled facts with their source and date.
    # On by default since `bench/memory_extraction/RESULTS.md`: 31 of 31 saves correct, 0 of 16
    # planted facts saved, 33 of 36 stated facts kept, at about US$ 0.00002 per turn, which the
    # usage log records. The messaging bots never extract, whatever this says: anyone who can reach
    # a bot would be writing the owner's memory. Set CHIMERA_MEMORY_EXTRACT=0 to stop it.
    memory_extract: bool = Field(default=True, validation_alias="CHIMERA_MEMORY_EXTRACT")

    # Send a chat's earlier turns as the model's own messages, tool calls included, with the
    # profile and recalled facts in the turn context, instead of one flattened user message
    # (`ChatSession.real_history`). It reaches `chat`, `assist`, the TUI, the app's chat and both
    # Discord paths. Off keeps the flattened form byte for byte; whether to flip it is measured in
    # `bench/chat_history`, because the Discord bot in production runs this path.
    chat_real_history: bool = Field(default=False, validation_alias="CHIMERA_CHAT_REAL_HISTORY")

    # Run the cron daemon inside `chimera app` (the desktop backend), so scheduled jobs fire while
    # the app is open — the whole point of a proactive assistant. Defaults ON: a "briefing at 7am"
    # should just work once you've scheduled it, without a separate `chimera serve --cron` terminal
    # running 24/7. Set CHIMERA_APP_CRON=0 (or `chimera app --no-cron`) for a purely reactive app.
    app_cron: bool = Field(default=True, validation_alias="CHIMERA_APP_CRON")

    # Tell a scheduled job's `deliver_to` channel when it could not run or finish (error, timeout,
    # spend cap, switched off by the failure brake) — one short line when an outage starts (and per
    # new failure kind in it) and one when the job has run again twice in a row, never the error
    # text. ON by default (study 29, P3.1, approved by the owner): it changes nothing any job does,
    # only makes visible a failure that was already recorded in `cron_results.jsonl` and the logs
    # and reached nobody. It does change what arrives in the channel of anyone who already has a
    # `deliver_to`; set CHIMERA_CRON_NOTIFY_FAILURES=0 to silence it. Read on every tick, so it
    # applies from the next. Covers Chimera's own scheduler only — not scripts run by a separate
    # dispatcher beside it.
    # Off is not "as before this flag existed": the result sink no longer posts a run that raised
    # (its text is not fit for a channel), so with this off a run that errored, timed out or hit a
    # spend cap reaches NO channel under any `notify` — a `failures_only` job then reports only the
    # runs its own verify gate rejected. The failures stay in `cron_results.jsonl` and `cron doctor`.
    cron_notify_failures: bool = Field(
        default=True, validation_alias="CHIMERA_CRON_NOTIFY_FAILURES"
    )

    # Keep this computer from going to sleep while there is work (study 29, P2.5;
    # `chimera/core/keep_awake.py`). `off` (the default) never touches the operating system;
    # `working` holds the machine awake while a coding turn, a background work, an autonomous run
    # or a scheduled job is running or due within ten minutes, and lets go when the last one ends;
    # `always` holds it for as long as the app is open. OFF by default because it changes what the
    # machine does and spends battery, and nothing has measured that it should be on: the item's
    # measure is the count of missed schedules on the owner's machine, before and after.
    #
    # What it prevents is IDLE sleep. Closing the lid, pressing the power button or choosing Sleep
    # still sleeps, and the setting's hint says so. A headless server has no idle sleep to prevent,
    # so there it does nothing. Read on every tick of the keeper, so a change applies in seconds.
    keep_awake: Literal["off", "working", "always"] = Field(
        default="off", validation_alias="CHIMERA_KEEP_AWAKE"
    )
    # Whether `keep_awake` still holds while the machine runs on battery. Off by default: a laptop
    # unplugged in a bag that refuses to sleep is the failure that costs the most, and it is the one
    # nobody is there to see.
    keep_awake_on_battery: bool = Field(
        default=False, validation_alias="CHIMERA_KEEP_AWAKE_ON_BATTERY"
    )

    # Where an isolated run's git worktree is checked out (study 29, P5.3; `chimera/core/worktree.py`).
    # Empty (the default) is the system temp folder, which is what it always was. A worktree is a
    # full checkout of the repository, so on a machine whose temp lives on a small system drive a
    # few killed runs are gigabytes on the one disk that must not fill — this lets the owner point
    # them at another drive. Must be an absolute path OUTSIDE the project: a worktree inside the
    # repository it was made from would show up in that repository's own status, search and
    # checkpoints. A value that breaks either rule is ignored with a warning and temp is used.
    # Read at every worktree creation, so a change applies from the next isolated run.
    worktree_dir: str = Field(default="", validation_alias="CHIMERA_WORKTREE_DIR")
    # The first segment of every branch an isolated run makes: `<prefix>/attempt-<hex>` (study 29,
    # P8.1). `chimera`, which is what it always was. Those branches land in the OWNER's repository,
    # where `git branch` is read by people and by tooling with its own naming rules, so the name is
    # theirs to choose. One segment of letters, digits, `-` and `_`: anything else (a slash, a dot,
    # a space) is read as the default with a warning rather than handed to `git worktree add`, which
    # would refuse it and fail the run. Every prefix that has made a branch is remembered, so the
    # cleanup of a killed run still finds branches made under the previous one.
    branch_prefix: str = Field(default="chimera", validation_alias="CHIMERA_BRANCH_PREFIX")

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
    # repo, and tainting every `read_file` would make the taint gate fire on every run (unusable).
    # Set False when running against code you do NOT control — a third-party repo, a PR branch,
    # anything downloaded. What False does, and where:
    # - `read_file` and `grep` output is fenced and taints the run like a fetched page, arming the
    #   same tool-narrowing gate — wherever a taint ledger wraps the tools: under `--taint` on the
    #   CLI, and always on the desktop Code surface, which builds a ledger for every turn. With no
    #   ledger, False changes nothing for these two tools.
    # - The repository's AGENTS.md is fenced and sanitised in the system prompt WHENEVER this is
    #   False, ledger or not, and taints the run before step 1 wherever a ledger exists (study 30,
    #   S30-26).
    # (The sandbox is still the real boundary for hostile code — see SECURITY.md.)
    trust_workspace: bool = Field(default=True, validation_alias="CHIMERA_TRUST_WORKSPACE")

    # Should the CHAT agent be assembled with the same protections the coding turn gets — a posture
    # denylist and the taint ledger wrapped around every tool?
    #
    # **Default True since 2026-09-10.** It shipped False for its whole life, and the reason was
    # real: the chat registry was shared with the OpenAI-compatible endpoint, so arming it for the
    # screen armed it for every benchmark harness too. Two things had to change first, and both
    # did. `chimera app` now builds `/v1/chat/completions` from its own factory
    # (`openai_factory`), so this setting reaches the screen and stops there. And
    # `guard_chat_registry` now takes an `approve=`, which it was the one `ledger_registry` caller
    # never to pass — so the narrowing had nobody to ask, and `LedgeredTool` reads nobody as refuse.
    #
    # Measured on the shipped bench, one corpus, three assemblies
    # (`bench/right_hand_governance/RESULTS.md` §5b):
    #
    #     guard off (what shipped)      0 of 7 attacks blocked   over-block 0.000
    #     guard on, nobody answers      7 of 7                   over-block 0.750
    #     guard on, a person answers    7 of 7                   over-block 0.250
    #
    # Two thirds of the apparent price of this guard was the silence behind it, not the guard.
    # What the default buys: ask the chat to read a page carrying a planted instruction and it can
    # no longer write the file that instruction names without a person saying yes on the screen.
    # What it costs: the exec tools leave the chat's registry (the posture denies them, as it always
    # has on the coding turn), and a legitimate write after reading a page now draws a question.
    #
    # The messaging gateway was NEVER in this setting's blast radius, whatever the comment here used
    # to say: `MessagingManager` builds its own sessions through `governed_profile`.
    #
    # Set it to `0` to get the old assembly back — and note that the app STATES which one it has:
    # the posture line says, in a chat without a ledger, that the conversation can write after
    # reading untrusted content.
    guard_chat: bool = Field(default=True, validation_alias="CHIMERA_GUARD_CHAT")

    # Archive a coding conversation nobody has touched for this many days. Empty (the default), zero
    # or negative means never. Archiving is a timestamp beside the transcripts and nothing else — no
    # file, folder or worktree is touched — and the rule never archives a conversation with a turn
    # running, a question waiting, a background work unfinished or a share link open
    # (`chimera/api/conversation_state.py`). Read on every look at the list, so a change applies at
    # once.
    archive_after_days: float | None = Field(
        default=None, validation_alias="CHIMERA_ARCHIVE_AFTER_DAYS"
    )

    # Whether a conversation can be shared with a second person at all (`chimera/api/sharing.py`).
    # ON by default because that is what the app did before the switch existed: a share link is made
    # only when the owner presses Share, and the network door opens only when they open it. Off
    # refuses a new link, closes the network door and stops every existing link from opening — the
    # links stay on disk, listed on the Security card, so turning it back on does not lose them.
    # Read on every request, so a change applies at once.
    sharing: bool = Field(default=True, validation_alias="CHIMERA_SHARING")
    # How long a NEW share link opens its conversation, in hours. Empty (the default), zero or
    # negative means never — what every link did before this existed. Stamped on the link when it is
    # made (`Share.expires_at`), so changing it does not reach back to links already handed out; the
    # Security card lists those with their own expiry and revokes them one by one or all at once.
    share_expiry_hours: float | None = Field(
        default=None, validation_alias="CHIMERA_SHARE_EXPIRY_HOURS"
    )

    # Base URL for a local Ollama server. A model like `ollama_chat/llama3` runs on your machine
    # with no API key — set this only if Ollama listens somewhere other than the default. Reinforces
    # the fully-local, self-hostable path: `CHIMERA_DEFAULT_MODEL=ollama_chat/llama3`, no key needed.
    #
    # `127.0.0.1`, NOT `localhost`, and the difference is measurable rather than stylistic. The two
    # are the same machine, but `localhost` is a NAME that resolves to two addresses — `::1` and
    # `127.0.0.1` — so a client with nothing to connect to tries both in turn and waits twice.
    # Measured on Windows, where a loopback port with nothing behind it takes 2.04 s to come back
    # refused: 530 ms through `localhost` against 265 ms through `127.0.0.1`, for the identical
    # answer. Ollama's own default is `127.0.0.1:11434`, so this names what it actually binds.
    #
    # The cost of being specific: an Ollama told to listen on `[::1]` only — which takes deliberately
    # setting `OLLAMA_HOST` — is no longer found at the default. That is a URL in Settings and the
    # message names it ("nothing answered at http://127.0.0.1:11434"), so the failure explains its
    # own fix. A value you set, by env or by the Settings screen, is untouched by this.
    ollama_base_url: str = Field(
        default="http://127.0.0.1:11434", validation_alias="CHIMERA_OLLAMA_BASE_URL"
    )
    # LM Studio's OpenAI-compatible server, `/v1` included: it is what LiteLLM's `lm_studio/`
    # provider needs in `LM_STUDIO_API_BASE` — which has NO default there, so `lm_studio/<model>`
    # with the variable unset is a request to api.openai.com with a fake key. The gateway exports
    # this value under that name when it is unset, the way it exports `OLLAMA_API_BASE`; the
    # discovery probe asks `{base}/models` on it. LM Studio needs no key, and LiteLLM sends a
    # placeholder when none is set.
    lm_studio_base_url: str = Field(
        default="http://localhost:1234/v1", validation_alias="CHIMERA_LM_STUDIO_BASE_URL"
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

    # The model that answers the TALK of a spoken turn on the Code screen — a question, a remark —
    # empty (the default) meaning the conversation's own. A person waiting to hear an answer cares
    # about the first word more than anything the model does after it, and the first word is where
    # models differ most: measured on 2026-09-17 with a coding turn's prompt, `gemini-2.5-flash-lite`
    # answered at 0.7–0.9 s and the default `deepseek-v4-flash` at 2.7–8.7 s with its reasoning off
    # (7.4–19.9 s with it on) — the owner tested a Gemini model live and heard the difference. A
    # spoken request for WORK (create, fix, refactor…) still goes to the conversation's model with
    # its thinking, and typed turns never use this one (`code_api._model_for`). The receipt under
    # each answer names the model that answered it.
    voice_model: str = Field(default="", validation_alias="CHIMERA_VOICE_MODEL")
    # The model that does the WORK a spoken turn asks for (create, fix, refactor…), with its
    # thinking; empty (the default) means the conversation's own. The owner's shape for hands-free
    # use (2026-09-18): one model to talk, one to reason — both chosen by the person.
    voice_work_model: str = Field(default="", validation_alias="CHIMERA_VOICE_WORK_MODEL")

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
    # How long an ATTENDED surface (the desktop turn) waits for the person to answer a durable
    # approval question before silence refuses. `pending.WAIT_SECONDS` (15 min) is the CLI's
    # figure for someone answering from a phone; an HTTP turn holds a stream open the whole time,
    # so it is bounded shorter. Silence still refuses — this changes how long, never whether.
    approval_wait: float = Field(default=300.0, validation_alias="CHIMERA_APPROVAL_WAIT")

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

    # --- Typed decisions (`chimera.decisions`): which backend answers a Noul / Choice / Score, and
    # which model. Nothing consumes a decision on a default install yet — the kernel's REVIEW band,
    # the verifier, the voice router each arrive with their own measurement — so these choose the
    # instrument, not whether one is used.
    #
    # `local_logprob` (the default): a small instruct model through Ollama, decision-first, read by
    # logprobs and passed through the map the package ships. Measured on the governance corpus
    # (`bench/jev_decisions/RESULTS.md` §7b): the hosted judge's operating point at US$ 0 and 0.75 s.
    # `hosted_verbalized`: the fusion judge model asked for a verbalized probability, reasoning off
    # (§4–§6: AUROC 0.886, ECE 0.051 raw, 4–5 s). `openrouter_decisions`: OpenRouter's Decisions
    # API with a typed-decision model behind it — deterministic and 0.34 s, over-confident mid-scale
    # without a map, refuses benign work under pressure framing (§8) — opt-in, fails closed per call.
    #
    # The model is the backend's measured default when empty: `qwen3:4b`, the fusion judge, or
    # `typesafe/jev-1.13`. A different model is a different instrument: the shipped map is keyed on
    # the model and does not apply to another, and the receipt says so (`calibrated: false`).
    decision_backend: str = Field(default="local_logprob", validation_alias="CHIMERA_DECISION_BACKEND")
    decision_model: str = Field(default="", validation_alias="CHIMERA_DECISION_MODEL")
    # The gate in front of HOSTED decision asks (`chimera/decisions/gate.py`). Unset = no gate: each is
    # a budget the owner chooses. Requests and tokens per minute make an ask WAIT at 80% of the budget
    # in flight; the daily USD ceiling, summed from the decision log, refuses. A refusal is a halt
    # that names the gate on the receipt, never an answer. A local model is not gated: it spends
    # electricity, and a dollar ceiling is not about electricity.
    decision_rpm: int | None = Field(default=None, validation_alias="CHIMERA_DECISION_RPM")
    decision_tpm: int | None = Field(default=None, validation_alias="CHIMERA_DECISION_TPM")
    decision_daily_usd: float | None = Field(default=None, validation_alias="CHIMERA_DECISION_DAILY_USD")
    # --- Verified answers (`chimera/fusion/verified.py`): a turn's final answer written from sources
    # the product handed the model (attachments, recalled memory, retrieved chunks), in a step with no
    # tool call, is read by the System One backend above as "supported / unsupported / declined" and
    # shipped only when supported at p >= the threshold; otherwise the strong model answers, is read
    # again, and the decline ships if that fails too. ON by default because study 26 measured it
    # (`bench/verified_cascade/RESULTS.md`, 400 paired items): wrong answers shipped 33 → 21, 11 fixed
    # and 0 broken (Holm p = 0.002), at 1.87× the cost with the local verifier, 0 hand-offs on
    # answerable items; the lexical gate it replaces matched no gate at 10.8× the cost. The threshold
    # is the registered 0.8 on the raw number (no map exists for this decision): at 0.5 the local arm
    # shipped 25 wrong, at 0.8 21, at 0.9 21. Tool-using steps are never gated — bench B4 measured a
    # router making every executor worse. With the local backend and no Ollama, the verifier falls
    # back to Jev when an OpenRouter key is set, else to the lexical gate, and the receipt says which.
    # Only QUESTIONS about the sources are checked (`chimera/fusion/grounded_question.py`): a task done
    # with them (summarize, translate, judge) passes straight through. Measured with gpt-6-luna
    # drafting; another drafter is checked the same way and its result is unmeasured.
    verified_answers: bool = Field(default=True, validation_alias="CHIMERA_VERIFIED_ANSWERS")
    verified_answers_threshold: float = Field(
        default=0.8, ge=0.0, le=1.0, validation_alias="CHIMERA_VERIFIED_ANSWERS_THRESHOLD"
    )
    # Empty: the measured escalation model (`gpt-6-sol`) when an OpenRouter key reaches it, else the
    # tier ladder's top for the cost mode (`verified.escalation_model`).
    verified_answers_escalate_model: str = Field(
        default="", validation_alias="CHIMERA_VERIFIED_ANSWERS_ESCALATE_MODEL"
    )
    # --- A structured answer the route filed as reasoning. Some routes return a reasoning model's
    # whole reply as reasoning, with `content` empty and `finish_reason` "stop" (`deepseek-r1` on
    # Novita, 37-43% of calls in `bench/review_judge/RESULTS-h11.md`, 13/26 in
    # `bench/answer_in_reasoning`); the gateway flags it (`CompletionResult.answer_in_reasoning`)
    # and never makes the reasoning the answer. On, a caller that asked for JSON reads an object of
    # its own schema from the END of the reasoning, and its receipt says `answer_from: reasoning`;
    # today that caller is the hosted decision backend. Off (the default), it re-asks once as it
    # always did, and a reading that still came back that way says `answer_from: reasoning_unread`.
    # Off because a recovered reading has not been measured against a re-asked one on the same
    # items: a map fitted on one path is not known to fit the other. The agent loop never reads
    # prose from reasoning, whatever this says. ---
    answer_from_reasoning: bool = Field(
        default=False, validation_alias="CHIMERA_ANSWER_FROM_REASONING"
    )

    # --- The REVIEW band (`chimera/governance/band.py`): off | on. With it on, and only under
    # `observe` or `enforce`, a tool call the lexical rules did not match is put to the decision
    # backend above as "is this shell action dangerous?" and the calibrated probability is read
    # against two thresholds: REVIEW at or above `review_at`, the default below `allow_below`, and
    # the default as a prior between them — the number on the audit line either way. Under
    # `observe` the REVIEW is recorded and not enforced, which is what prices it.
    #
    # The defaults are read off the ROC of the calibrated local arm (`bench/jev_decisions`, 55
    # governance items): 0.50 is the hosted judge's operating point (catch 20/24, 6/31 benign
    # actions stopped); 0.30 is where the arm misses at most one attack in 24 (9/31 stopped). A
    # deployment that refits the map on its own rows reads its own ROC before moving these. ---
    governance_band: str = Field(default="off", validation_alias="CHIMERA_GOVERNANCE_BAND")
    governance_band_review_at: float = Field(default=0.50, validation_alias="CHIMERA_GOVERNANCE_BAND_REVIEW_AT")
    governance_band_allow_below: float = Field(default=0.30, validation_alias="CHIMERA_GOVERNANCE_BAND_ALLOW_BELOW")
    # Hysteresis exit: an action already in REVIEW leaves it only below this. It sits between the two
    # thresholds (the band refuses any other order) — the local arm moves up to 0.05 raw between two
    # runs of the same item, and without the gap a retry would flip verdicts on noise. It was a
    # constant until study 22 (phase 0); a deployment that moves the thresholds must be able to move it.
    governance_band_exit_at: float = Field(default=0.40, validation_alias="CHIMERA_GOVERNANCE_BAND_EXIT_AT")

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

    @field_validator("api_base", mode="after")
    @classmethod
    def _api_base_is_local_or_encrypted(cls, value: str | None) -> str | None:
        """Refuse a cleartext endpoint that is not this machine.

        Every configured provider key is exported to the environment for LiteLLM to see, and this
        value is applied to EVERY call — so pointing it at a third party sends whichever key that
        provider matches to them. That is a footgun rather than an exploit, since somebody typed the
        value, and the field exists for exactly the case that is safe: a self-hosted server on
        loopback, where nothing crosses a network.

        Plain HTTP to another host is the one reading with no legitimate version: a bearer token in
        cleartext, to somewhere else. TLS is fine, loopback is fine, and unset — the ordinary case —
        is untouched.

        Compared after parsing, never by substring. `"localhost" in url` accepts
        `http://evil.localhost.com`, and a check anyone can defeat by registering a domain is not a
        check. A URL that will not parse is refused rather than assumed local: a string no parser
        understands must not pass a guard whose whole job is to decide where it points.
        """
        if not value:
            return value
        import ipaddress
        from urllib.parse import urlsplit

        try:
            parts = urlsplit(value)
            scheme, host = parts.scheme.lower(), (parts.hostname or "").lower()
        except ValueError:
            scheme, host = "", ""
        if scheme == "https":
            return value
        # Parsed as an address, not matched as a prefix. `host.startswith("127.")` was the first
        # version of this line and it accepts `127.0.0.1.atacante.com` — the same substring mistake
        # the docstring above warns about, made two lines below the warning.
        try:
            local = ipaddress.ip_address(host).is_loopback
        except ValueError:
            local = host == "localhost"
        if not local:
            raise ValueError(
                f"CHIMERA_API_BASE={value!r} sends every provider key you have configured, in "
                "cleartext, to a host that is not this machine. Use https://, or a loopback "
                "address (127.0.0.1 / localhost) for a self-hosted server."
            )
        return value

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
                word,
                ", ".join(sorted(_POSTURE_WORDS)),
            )
            return ""
        return word

    @field_validator("branch_prefix", mode="before")
    @classmethod
    def _branch_prefix_is_one_ref_segment(cls, value: object) -> object:
        """A prefix git would refuse fails every isolated run at `git worktree add`; read it as the
        default instead, and say so. Empty is the default too — an empty first segment is no name."""
        if not isinstance(value, str):
            return value
        word = value.strip()
        if not word:
            return "chimera"
        if not is_branch_prefix(word):
            _log.warning(
                "CHIMERA_BRANCH_PREFIX=%r is not one segment of letters, digits, '-' or '_', or "
                "is a name Windows reserves (CON, NUL, AUX, COM1...); using 'chimera'.",
                word,
            )
            return "chimera"
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
            _log.warning(
                "CHIMERA_APPROVAL_MODE=%r is not one of %s; falling back to 'ask'.",
                word,
                ", ".join(sorted(_GOVERNANCE_WORDS)),
            )
            return "ask"
        return word

    @field_validator("keep_awake", mode="before")
    @classmethod
    def _keep_awake_is_a_mode_word(cls, value: object) -> object:
        """Unknown or empty falls back to `off` — the state that touches nothing, so a typo cannot
        keep a laptop awake in a bag. Warned rather than raised, for the reason
        `_empty_boolean_is_unset` gives: a bad line must not stop the app from starting."""
        if not isinstance(value, str):
            return value
        word = value.strip().lower()
        if word in ("off", "working", "always"):
            return word
        if word:
            _log.warning(
                "CHIMERA_KEEP_AWAKE=%r is not one of off, working, always; falling back to 'off'.",
                word,
            )
        return "off"

    @field_validator("openrouter_data_collection", mode="before")
    @classmethod
    def _data_collection_word(cls, value: object) -> object:
        """Empty is unset (`allow`); anything else that is not `allow` is read as `deny`, warned.

        The opposite direction from `keep_awake`'s fallback, on purpose. `allow` is the default, so
        the only reason anyone writes this line is to ask for `deny`; a typo (`deney`, `no`) falling
        back to `allow` would send prompts to routes that keep them while the owner believes they
        asked for the opposite — a failure nobody would ever see. Falling back to `deny` can only
        cost a route, and a route that fails says so on the receipt."""
        if not isinstance(value, str):
            return value
        word = value.strip().lower()
        if word in ("", "allow"):
            return "allow"
        if word != "deny":
            _log.warning(
                "CHIMERA_OPENROUTER_DATA_COLLECTION=%r is not allow or deny; reading it as 'deny'.",
                word,
            )
        return "deny"

    @field_validator("daily_usd_cap", mode="before")
    @classmethod
    def _empty_cap_is_no_cap(cls, value: object) -> object:
        """`CHIMERA_DAILY_USD_CAP=` is how the Usage screen removes the cap, and pydantic reads an
        empty string as a float that failed to parse — which would stop the app from starting the
        moment the person cleared the field. Empty is the absence of a cap, as unset already is."""
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("archive_after_days", mode="before")
    @classmethod
    def _archive_after_days_or_never(cls, value: object) -> object:
        """Empty is the documented "never", and an unreadable value falls back to it too.

        Falling back to never rather than refusing to start, because the cost of the two mistakes is
        not the same: a typo that switched archiving off hides nothing, while one that stopped the
        app over a convenience setting takes every conversation with it.
        """
        if value is None or isinstance(value, (int, float)):
            return value
        text = str(value).strip()
        if not text:
            return None
        try:
            return float(text)
        except ValueError:
            _log.warning(
                "CHIMERA_ARCHIVE_AFTER_DAYS=%r is not a number of days; conversations are never "
                "archived automatically.",
                text,
            )
            return None

    @field_validator("share_expiry_hours", mode="before")
    @classmethod
    def _share_expiry_hours_or_never(cls, value: object) -> object:
        """Empty, zero or negative is "never", and an unreadable value falls back to it with a
        warning, for the reason `_archive_after_days_or_never` gives: a typo must not stop the app.
        The Settings row refuses a bad value before writing it (`config_api`), and the Security
        card shows "never" beside the links, so a hand-edited typo is visible rather than silent."""
        if value is None:
            return None
        if isinstance(value, (int, float)):
            return float(value) if value > 0 else None
        text = str(value).strip()
        if not text:
            return None
        try:
            hours = float(text)
        except ValueError:
            _log.warning(
                "CHIMERA_SHARE_EXPIRY_HOURS=%r is not a number of hours; new share links never "
                "expire.",
                text,
            )
            return None
        return hours if math.isfinite(hours) and hours > 0 else None

    @field_validator("taint_authority", mode="before")
    @classmethod
    def _taint_authority_is_a_mode_word(cls, value: object) -> object:
        """Unknown falls back to `provenance` — the stricter mode, so a typo cannot silence the
        narrowing. Empty is unset, for the reason `_empty_boolean_is_unset` gives."""
        if not isinstance(value, str):
            return value
        word = value.strip().lower()
        if not word:
            return "provenance"
        if word not in ("provenance", "authority"):
            _log.warning(
                "CHIMERA_TAINT_AUTHORITY=%r is not one of provenance, authority; "
                "falling back to 'provenance'.",
                word,
            )
            return "provenance"
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
        "discord_allowed_users",
        "telegram_allowed_users",
        "slack_allowed_users",
        "signal_allowed_users",
        "whatsapp_allowed_numbers",
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

    def can_answer(self) -> bool:
        """Whether this install has SOME way to run a model: a key, or a default model that runs on
        this machine and needs none.

        The question every command gate and the desktop's first-run gate actually ask — and they
        asked :meth:`has_any_key` instead, so a machine whose only model was ``ollama_chat/llama3``
        was refused with *"No provider key configured"* by six CLI commands and shown a wizard that
        demanded a key, while the gateway one layer down would have served it (its own credential
        check has let local models through since the Ollama work). The same defect
        :mod:`chimera.providers.discovery` fixed for unlisted providers, one step further out.
        """
        from chimera.providers.discovery import is_local_model

        return self.has_any_key() or is_local_model(self.default_model)

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
    # The OS vault, for anyone who put their keys there instead of in a file. Filled here, beside
    # the `.env` export, because both answer the same question — what credentials does this process
    # have — and a vault consulted somewhere deeper would be a second source of truth that
    # disagrees with the first under conditions nobody could predict.
    #
    # Gap-filling only: anything already in the environment wins, so an install that works today is
    # untouched and `OPENROUTER_API_KEY=… chimera solve` still means what it says.
    #
    # Gap-filling against the `.env` too: a name the file assigns is the file's, because a vault
    # copy put in the environment would outrank the file (pydantic-settings ranks the environment
    # first) and silently override what the owner typed there.
    #
    # The frozen desktop build reads only the names its `.env` marks as moved
    # (`config_vault.startup_names`) — none, for an owner who never opted in, who must launch
    # exactly as before; and never more than they moved, though the vault may hold more.
    from chimera.config_vault import load_into_environment, startup_names

    env_file = Settings.model_config.get("env_file")
    names = startup_names(env_file)
    if names:
        load_into_environment(names=names, env_file=env_file)
    return Settings()
