"""A receipt is priced from the provider's index in EVERY entrypoint that runs a turn, not only `serve`.

Found 2026-11-16 by the planner A/B (bench/planner_ab, Amendment 1): `chimera solve` receipts for
`openrouter/z-ai/glm-5.3-flash` came out about 39x too dear (845k in + 89k out metered at US$ 1.575,
list price 0.041). Two defects stacked:

1. The price warm-up was started only by `serve`. `solve`, `chat`, `run` and the benches never
   fetched the index, so in a data folder with no `model-prices.json` the receipt fell to the table.
2. The table's fallback was wrong for this slug. The catalogue registered `z-ai/glm-5.3` as a
   substring pattern, which is inside `z-ai/glm-5.3-flash`, and being registered later it won: the
   flash model was priced as the flagship (1.40 / 4.40), not by its own catalogue row.

The warm-up must never be waited for: a gateway is built inside event loops and on the way to a
prompt. It starts a thread; a receipt is priced when its turn ends, from whatever has landed by then.
A failed fetch is remembered on disk so an offline machine does not retry on every process.

The autouse fixture in conftest silences the warm-up; these tests put a real one back (a thread that
runs `warm_price_cache`) with the network stubbed: no test may reach OpenRouter.
"""

from __future__ import annotations

import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from chimera.config import Settings, get_settings
from chimera.fusion.receipts import ModelPrice, resolve_price, set_price
from chimera.orchestration.budget import (
    SpendBudget,
    SpendCappedBackend,
    SpendExceeded,
    worst_case_usd,
)
from chimera.orchestration.metering import MeteredBackend
from chimera.orchestration.receipts import price_completion
from chimera.providers import listing
from chimera.providers.catalog import CATALOG
from chimera.providers.gateway import CompletionResult, LLMGateway, MessageLike
from chimera.providers.listing import PRICE_CACHE_NAME, ModelOption

GLM_FLASH = "openrouter/z-ai/glm-5.3-flash"
LIVE = (0.0399, 0.0784)


def _live_model() -> ModelOption:
    return ModelOption(
        slug=GLM_FLASH, label="GLM Flash", vendor="z-ai", source="openrouter",
        input_per_m=LIVE[0], output_per_m=LIVE[1], vision=False, tools=True, context_k=1048,
    )


def _real_warmup(settings: Any) -> threading.Thread:
    """The seam as shipped (conftest replaces it with a no-op), minus the real network."""
    thread = threading.Thread(
        target=listing.warm_price_cache, args=(settings,), name="price-warmup", daemon=True
    )
    thread.start()
    return thread


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path))
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    get_settings.cache_clear()
    listing._warm_state.clear()
    monkeypatch.setattr(listing, "openrouter_models", lambda *a, **k: ((_live_model(),), ""))
    monkeypatch.setattr(listing, "start_price_warmup", _real_warmup)
    return tmp_path


def _settings(home: Path, key: str = "sk-or-test") -> Settings:
    return Settings(CHIMERA_HOME=str(home), OPENROUTER_API_KEY=key)


def _join_warmup(home: Path) -> None:
    thread = listing._warm_state.get(home.resolve())
    if thread is not None:
        thread.join(timeout=10)
        assert not thread.is_alive()


def _catalogue_row() -> Any:
    return next(e for e in CATALOG if e.slug == GLM_FLASH)


def test_without_an_index_the_flash_model_is_priced_by_its_own_row_not_the_flagships(home: Path) -> None:
    price = resolve_price(GLM_FLASH)
    row = _catalogue_row()

    assert price is not None
    assert (price.input_per_m, price.output_per_m) == (row.input_per_m, row.output_per_m)
    assert price.input_per_m < 1.0, "priced as glm-5.3 (1.40/4.40), the 39x overstatement"


@pytest.mark.parametrize("before_first_lookup", [True, False])
def test_an_explicit_override_beats_a_catalogue_row_in_any_order(
    home: Path, before_first_lookup: bool
) -> None:
    mine = ModelPrice(7.0, 9.0)
    if not before_first_lookup:
        assert resolve_price(GLM_FLASH) is not None  # folds the catalogue in first
    set_price("glm-5.3-flash", mine)  # a substring override, shorter than the catalogue's pattern

    assert resolve_price(GLM_FLASH) == mine


def test_building_a_gateway_does_not_wait_for_the_index(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    release = threading.Event()

    def slow(*a: Any, **k: Any) -> Any:
        release.wait(timeout=10)
        return ((_live_model(),), "")

    monkeypatch.setattr(listing, "openrouter_models", slow)
    started = time.monotonic()
    LLMGateway(_settings(home))
    elapsed = time.monotonic() - started
    try:
        assert elapsed < 0.5, f"the constructor waited {elapsed:.2f}s for the index"
        # The index has not landed: the receipt falls back to the reserve row, as before.
        early = resolve_price(GLM_FLASH)
        assert early is not None and early.output_per_m == _catalogue_row().output_per_m
    finally:
        release.set()
    _join_warmup(home)

    # A turn that ends after the index arrived is priced from it.
    late = resolve_price(GLM_FLASH)
    assert late is not None and (late.input_per_m, late.output_per_m) == LIVE


def test_a_failed_fetch_is_not_retried_by_the_next_process(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []

    def offline(*a: Any, **k: Any) -> Any:
        calls.append(1)
        return ((), "unreachable")

    monkeypatch.setattr(listing, "openrouter_models", offline)
    listing.warm_prices_for_turns(_settings(home))
    _join_warmup(home)
    assert len(calls) == 1
    assert (home / listing.PRICE_BACKOFF_NAME).is_file()

    listing._warm_state.clear()  # a new process
    listing.warm_prices_for_turns(_settings(home))
    _join_warmup(home)
    assert len(calls) == 1, "an offline machine asked again inside the retry window"

    old = time.time() - listing.PRICE_RETRY_AFTER_S - 10
    os.utime(home / listing.PRICE_BACKOFF_NAME, (old, old))
    listing._warm_state.clear()
    listing.warm_prices_for_turns(_settings(home))
    _join_warmup(home)
    assert len(calls) == 2, "the retry deadline never expired"


def test_a_success_clears_the_backoff(home: Path) -> None:
    backoff = home / listing.PRICE_BACKOFF_NAME
    backoff.write_text("x", encoding="utf-8")
    old = time.time() - listing.PRICE_RETRY_AFTER_S - 10
    os.utime(backoff, (old, old))

    listing.warm_prices_for_turns(_settings(home))
    _join_warmup(home)

    assert (home / PRICE_CACHE_NAME).is_file()
    assert not backoff.exists()


def test_a_key_added_later_in_the_same_process_still_warms(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    listing.warm_prices_for_turns(_settings(home, key=""))
    assert not listing._warm_state, "no key, yet it was treated as an attempt"

    listing.warm_prices_for_turns(_settings(home, key="sk-or-later"))
    _join_warmup(home)

    assert (home / PRICE_CACHE_NAME).is_file()


def test_a_running_warm_up_is_not_started_twice(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    release = threading.Event()
    calls: list[int] = []

    def slow(*a: Any, **k: Any) -> Any:
        calls.append(1)
        release.wait(timeout=10)
        return ((_live_model(),), "")

    monkeypatch.setattr(listing, "openrouter_models", slow)
    listing.warm_prices_for_turns(_settings(home))
    listing.warm_prices_for_turns(_settings(home))
    release.set()
    _join_warmup(home)

    assert len(calls) == 1


def test_a_fresh_cache_is_not_fetched_again_unless_forced(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert listing.remember_models((_live_model(),), home=home)
    calls: list[Any] = []
    monkeypatch.setattr(listing, "start_price_warmup", lambda s: calls.append(s))

    listing.warm_prices_for_turns(_settings(home))
    assert calls == []
    listing.warm_prices_for_turns(_settings(home), force=True)
    assert len(calls) == 1


def test_no_openrouter_key_means_no_request(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path))
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    get_settings.cache_clear()
    listing._warm_state.clear()

    def forbidden(*a: Any, **k: Any) -> Any:
        raise AssertionError("fetched the index without an OpenRouter key")

    monkeypatch.setattr(listing, "openrouter_models", forbidden)
    monkeypatch.setattr(listing, "start_price_warmup", _real_warmup)

    listing.warm_prices_for_turns(_settings(tmp_path, key=""))

    assert not (tmp_path / PRICE_CACHE_NAME).exists()


def _deny_openrouter(home: Path) -> None:
    policy = {"rules": [{"action": "provider.use", "resource": "openrouter", "effect": "deny"}]}
    (home / "policy.json").write_text(json.dumps(policy), encoding="utf-8")


def test_gateway_does_not_schedule_a_warmup_for_a_denied_provider(
    home: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    launches: list[Settings] = []
    monkeypatch.setattr(listing, "start_price_warmup", lambda settings: launches.append(settings))
    _deny_openrouter(home)

    LLMGateway(_settings(home))

    assert launches == []
    assert not (home / PRICE_CACHE_NAME).exists()


def test_a_queued_warmup_checks_policy_again_before_fetching(
    home: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    go = threading.Event()
    calls: list[int] = []

    def fetch(**kwargs: Any) -> tuple[tuple[ModelOption, ...], listing.Reason]:
        calls.append(1)
        return (_live_model(),), ""

    def queue(settings: Settings) -> threading.Thread:
        def worker() -> None:
            if go.wait(timeout=10):
                listing.warm_price_cache(settings)

        thread = threading.Thread(target=worker, name="price-warmup", daemon=True)
        thread.start()
        return thread

    monkeypatch.setattr(listing, "openrouter_models", fetch)
    monkeypatch.setattr(listing, "start_price_warmup", queue)
    listing.warm_prices_for_turns(_settings(home))
    try:
        _deny_openrouter(home)
    finally:
        go.set()
        _join_warmup(home)

    assert calls == []
    assert not (home / listing.PRICE_BACKOFF_NAME).exists()


def test_two_gateways_cannot_launch_while_the_first_thread_is_being_published(
    home: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    launched = threading.Event()
    duplicate = threading.Event()
    release_launch = threading.Event()
    release_fetch = threading.Event()
    launches: list[Settings] = []

    def start(settings: Settings) -> threading.Thread:
        launches.append(settings)
        (launched if len(launches) == 1 else duplicate).set()
        release_launch.wait(timeout=10)
        return _real_warmup(settings)

    def fetch(**kwargs: Any) -> tuple[tuple[ModelOption, ...], listing.Reason]:
        release_fetch.wait(timeout=10)
        return (_live_model(),), ""

    settings = _settings(home)
    monkeypatch.setattr(listing, "start_price_warmup", start)
    monkeypatch.setattr(listing, "openrouter_models", fetch)
    with ThreadPoolExecutor(max_workers=2) as pool:
        try:
            first = pool.submit(listing.warm_prices_for_turns, settings)
            assert launched.wait(timeout=5)
            second = pool.submit(listing.warm_prices_for_turns, settings)
            assert not duplicate.wait(timeout=0.2)
            release_launch.set()
            first.result(timeout=5)
            second.result(timeout=5)
        finally:
            release_launch.set()
            release_fetch.set()
    _join_warmup(home)
    assert len(launches) == 1
    assert (home / PRICE_CACHE_NAME).is_file()


@pytest.mark.parametrize("condition", ["missing", "empty", "expired"])
def test_a_previously_successful_warmup_rechecks_the_file(home: Path, condition: str) -> None:
    path = home / PRICE_CACHE_NAME
    settings = _settings(home)
    listing.warm_prices_for_turns(settings)
    _join_warmup(home)
    assert path.is_file()
    if condition == "missing":
        path.unlink()
    elif condition == "empty":
        path.write_text('{"models": {}}', encoding="utf-8")
    else:
        old = time.time() - listing.PRICE_CACHE_MAX_AGE_S - 10
        os.utime(path, (old, old))

    listing.warm_prices_for_turns(settings)
    _join_warmup(home)
    assert GLM_FLASH in json.loads(path.read_text(encoding="utf-8"))["models"]
    assert time.time() - path.stat().st_mtime < listing.PRICE_CACHE_MAX_AGE_S


def test_strict_planning_restarts_refresh_on_an_existing_gateway(home: Path) -> None:
    gateway = LLMGateway(_settings(home))
    _join_warmup(home)
    path = home / PRICE_CACHE_NAME
    path.unlink()

    gateway.planned_attempts(GLM_FLASH, max_tokens=100)
    _join_warmup(home)

    assert path.is_file()
    assert resolve_price(GLM_FLASH, require_fresh_openrouter=True) is not None


@pytest.mark.parametrize("failure", ["empty", "write"])
def test_empty_and_unwritten_indexes_back_off_and_retry(
    home: Path, monkeypatch: pytest.MonkeyPatch, failure: str,
) -> None:
    path = home / PRICE_CACHE_NAME
    backoff = home / listing.PRICE_BACKOFF_NAME
    settings = _settings(home)
    calls: list[int] = []

    def fetch(**kwargs: Any) -> tuple[tuple[ModelOption, ...], listing.Reason]:
        calls.append(1)
        return (() if failure == "empty" and len(calls) == 1 else (_live_model(),)), ""

    def fail_write(path: Path, data: str) -> None:
        raise OSError("simulated cache write failure")

    monkeypatch.setattr(listing, "openrouter_models", fetch)
    with monkeypatch.context() as patch:
        if failure == "write":
            patch.setattr(listing, "atomic_write_text", fail_write)
        listing.warm_prices_for_turns(settings)
        _join_warmup(home)
    assert backoff.is_file() and not path.exists()

    listing.warm_prices_for_turns(settings, force=True)
    assert calls == [1]
    old = time.time() - listing.PRICE_RETRY_AFTER_S - 10
    os.utime(backoff, (old, old))

    listing.warm_prices_for_turns(settings)
    _join_warmup(home)
    assert calls == [1, 1]
    assert path.is_file() and not backoff.exists()


@pytest.mark.parametrize("has_old_index", [False, True])
def test_strict_admission_refuses_an_underpriced_route_while_the_index_refreshes(
    home: Path, monkeypatch: pytest.MonkeyPatch, has_old_index: bool,
) -> None:
    slug = "openrouter/deepseek/deepseek-v3.2"
    fetching = threading.Event()
    publish = threading.Event()
    calls: list[int] = []

    def price(output: float) -> ModelOption:
        return ModelOption(
            slug=slug, label=slug, vendor="deepseek", source="openrouter",
            input_per_m=0.14, output_per_m=output,
        )

    def refresh(**kwargs: Any) -> tuple[tuple[ModelOption, ...], listing.Reason]:
        fetching.set()
        publish.wait(timeout=10)
        return (price(0.38),), ""

    class OfflineBackend:
        def complete(self, messages: list[MessageLike], **kwargs: Any) -> CompletionResult:
            calls.append(1)
            return CompletionResult(content="done", model=slug, prompt_tokens=0, completion_tokens=10_000)

    if has_old_index:
        assert listing.remember_models((price(0.28),), home=home)
        old = time.time() - listing.PRICE_CACHE_MAX_AGE_S - 10
        os.utime(home / PRICE_CACHE_NAME, (old, old))
    monkeypatch.setattr(listing, "openrouter_models", refresh)
    backend = OfflineBackend()
    capped = SpendCappedBackend(backend, SpendBudget(max_usd=0.003, strict=True))
    messages: list[MessageLike] = [{"role": "user", "content": "hi"}]

    LLMGateway(_settings(home))
    try:
        assert fetching.wait(timeout=5)
        fallback = resolve_price(slug)
        assert fallback is not None and fallback.output_per_m == 0.28
        with pytest.raises(SpendExceeded, match="unavailable or refreshing"):
            capped.complete(messages, model=slug, max_tokens=10_000)
        assert calls == []
    finally:
        publish.set()
        _join_warmup(home)

    with pytest.raises(SpendExceeded, match="may cost up to"):
        capped.complete(messages, model=slug, max_tokens=10_000)
    assert calls == []

    larger = SpendBudget(max_usd=0.004, strict=True)
    result = SpendCappedBackend(backend, larger).complete(messages, model=slug, max_tokens=10_000)
    assert calls == [1]
    assert larger.spent == price_completion(result).usd == 0.0038


def test_failed_refresh_keeps_the_old_file_for_receipts_and_out_of_strict_admission(
    home: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert listing.remember_models((_live_model(),), home=home)
    path = home / PRICE_CACHE_NAME
    previous = path.read_bytes()

    def fail_write(path: Path, data: str) -> None:
        raise OSError("simulated cache write failure")

    monkeypatch.setattr(listing, "atomic_write_text", fail_write)
    listing.warm_prices_for_turns(_settings(home), force=True)
    _join_warmup(home)

    assert path.read_bytes() == previous
    assert listing.known_price(GLM_FLASH) == LIVE
    assert resolve_price(GLM_FLASH, require_fresh_openrouter=True) is None


def test_a_family_substring_price_is_replaced_by_the_index_for_a_versioned_sibling(home: Path) -> None:
    """`deepseek-v3.2` has no catalogue row: the hand table's `deepseek-v3` substring answers
    (0.14/0.28, a guess about the family). Once the index is fetched, its number for the exact slug
    wins, which is the reason the fetch has to happen in every entrypoint."""
    slug = "openrouter/deepseek/deepseek-v3.2"
    assert not any(e.slug == slug for e in CATALOG)
    guess = resolve_price(slug)
    assert guess is not None and (guess.input_per_m, guess.output_per_m) == (0.14, 0.28)

    listing.remember_models(
        [
            ModelOption(
                slug=slug, label="V3.2", vendor="deepseek", source="openrouter",
                input_per_m=0.25, output_per_m=0.38, vision=False, tools=True, context_k=128,
            )
        ],
        home=home,
    )
    listing._index_cache = None

    live = resolve_price(slug)
    assert live is not None and (live.input_per_m, live.output_per_m) == (0.25, 0.38)

STRICT_SLUG = "openrouter/testvendor/quote-during-call"


def _quote_model(multiplier: float) -> ModelOption:
    return ModelOption(
        slug=STRICT_SLUG, label=STRICT_SLUG, vendor="testvendor", source="openrouter",
        input_per_m=0.1 * multiplier, output_per_m=0.4 * multiplier,
        cache_read_per_m=0.02 * multiplier, cache_write_per_m=0.18 * multiplier,
        long_prompt=(10, 0.3 * multiplier, 0.5 * multiplier, 0.05 * multiplier, 0.9 * multiplier),
    )


@pytest.mark.parametrize("path", ["wrapper", "agent"])
@pytest.mark.parametrize("provider_usd", [None, 0.009])
def test_strict_charges_the_tariff_it_reserved_even_when_a_new_index_arrives(
    home: Path, path: str, provider_usd: float | None,
) -> None:
    assert listing.remember_models((_quote_model(1.0),), home=home)

    class Backend:
        def planned_attempts(self, model: str, bound: int | None) -> list[tuple[str, int]]:
            return [(STRICT_SLUG, 1_000), (STRICT_SLUG, 1_000)]

        def complete(self, messages: list[MessageLike], **kwargs: Any) -> CompletionResult:
            assert listing.remember_models((_quote_model(10.0),), home=home)
            return CompletionResult(
                content="done", model=STRICT_SLUG, prompt_tokens=50, completion_tokens=1_000,
                cache_read_tokens=20, cache_write_tokens=10, usd=provider_usd,
                failed_attempts=[(STRICT_SLUG, 1_000)],
            )

    budget = SpendBudget(max_usd=0.002, strict=True)
    messages: list[MessageLike] = [{"role": "user", "content": "x" * 100}]
    backend = MeteredBackend(Backend())
    kwargs = {"model": STRICT_SLUG, "max_tokens": 1_000}
    worst_a = worst_case_usd(backend, messages, kwargs, strict=True)
    assert worst_a is not None and 0.0 < worst_a < budget.max_usd
    priced_answer = 0.000516 if provider_usd is None else provider_usd

    if path == "agent":
        from chimera.core.agent import Agent, AgentConfig, _UsageTally
        from chimera.tools.registry import ToolRegistry

        usage = _UsageTally()
        agent = Agent(backend, ToolRegistry(), AgentConfig(model=STRICT_SLUG))
        result = agent._step(messages, tools=None, on_token=None, usage=usage, spend=budget)
        assert usage.usd == pytest.approx(priced_answer)
    else:
        outside = MeteredBackend(SpendCappedBackend(backend, budget))
        result = outside.complete(messages, **kwargs)
        assert outside.usd == pytest.approx(priced_answer)

    assert backend.usd == pytest.approx(priced_answer)
    assert budget.spent == pytest.approx(priced_answer + worst_a / 2, abs=1e-6)
    cost = price_completion(result)
    assert cost.usd == pytest.approx(priced_answer)
    assert cost.estimated is (provider_usd is None)
    assert result.usd == provider_usd
    assert "_settled_cost" not in result.model_dump()
    assert resolve_price(STRICT_SLUG) is not None and listing.known_price(STRICT_SLUG) == (1.0, 4.0)
    worst_b = worst_case_usd(backend, messages, kwargs, strict=True)
    assert worst_b is not None and worst_b > budget.max_usd
    if provider_usd is None:
        assert budget.spent < budget.max_usd
    else:
        assert budget.blocked() is not None


def test_atomic_index_replacement_invalidates_a_cached_row_with_the_same_mtime(home: Path) -> None:
    model_a = _quote_model(1.0)
    assert listing.remember_models((model_a,), home=home)
    assert listing.known_price(STRICT_SLUG) == (0.1, 0.4)
    path = home / PRICE_CACHE_NAME
    stamp_a = path.stat()

    publisher = home / "publisher"
    assert listing.remember_models((replace(model_a, output_per_m=0.7),), home=publisher)
    replacement = publisher / PRICE_CACHE_NAME
    os.utime(replacement, ns=(stamp_a.st_atime_ns, stamp_a.st_mtime_ns))
    os.replace(replacement, path)

    stamp_b = path.stat()
    assert stamp_a.st_mtime_ns == stamp_b.st_mtime_ns
    assert stamp_a.st_size == stamp_b.st_size
    assert not os.path.samestat(stamp_a, stamp_b)
    assert listing.known_price(STRICT_SLUG) == (0.1, 0.7)


def test_normal_price_resolution_reads_base_cache_and_tier_from_one_index(
    home: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert listing.remember_models((_quote_model(1.0),), home=home)
    read_index = listing._load_price_index
    reads: list[Path] = []

    def publish_new_index_after_first_read(path: Path) -> Any:
        reads.append(path)
        snapshot = read_index(path)
        if len(reads) == 1:
            assert listing.remember_models((_quote_model(10.0),), home=home)
        return snapshot

    monkeypatch.setattr(listing, "_load_price_index", publish_new_index_after_first_read)

    quote_a = resolve_price(STRICT_SLUG)
    assert quote_a == ModelPrice(
        0.1, 0.4, cache_read_per_m=0.02, cache_write_per_m=0.18, long_prompt_over=10,
        long_prompt=ModelPrice(0.3, 0.5, cache_read_per_m=0.05, cache_write_per_m=0.9),
    )
    assert len(reads) == 1
    quote_b = resolve_price(STRICT_SLUG)
    assert quote_b is not None and quote_b.cache_read_per_m == 0.2
    assert quote_b.long_prompt is not None and quote_b.long_prompt.output_per_m == 5.0
    assert len(reads) == 2

@pytest.mark.parametrize("reported_usd", [None, 0.0042])
def test_hierarchy_fallback_receipt_agrees_with_the_settled_call(
    home: Path, reported_usd: float | None,
) -> None:
    from chimera.orchestration.artifacts import ArtifactStore
    from chimera.orchestration.hierarchy import HierarchicalOrchestrator
    from chimera.orchestration.receipts import load_delegations

    def model_price(out: float) -> ModelOption:
        return ModelOption(
            slug=STRICT_SLUG, label=STRICT_SLUG, vendor="testvendor", source="openrouter",
            input_per_m=0.0, output_per_m=out,
        )

    assert listing.remember_models((model_price(0.28),), home=home)

    class Backend:
        def planned_attempts(self, model: str, max_tokens: int | None) -> list[tuple[str, int]]:
            return [(model, 10_000)]

        def complete(self, messages: list[MessageLike], **kwargs: Any) -> CompletionResult:
            assert listing.remember_models((model_price(0.38),), home=home)
            return CompletionResult(
                content="42", model=STRICT_SLUG, prompt_tokens=15, completion_tokens=10_000,
                usd=reported_usd,
            )

    budget = SpendBudget(max_usd=0.003, strict=True)
    log = home / "delegations.jsonl"
    outcome = HierarchicalOrchestrator(
        SpendCappedBackend(Backend(), budget),
        weak_model=STRICT_SLUG, mid_model=STRICT_SLUG, top_model=STRICT_SLUG,
        store=ArtifactStore(home / "artifacts"), receipts_path=log,
    ).run("What is 6 × 7?")

    assert outcome.fell_back and outcome.answer == "42"
    [receipt] = outcome.receipts
    assert receipt.usd == budget.spent == (0.0028 if reported_usd is None else reported_usd)
    assert receipt.usd_estimated is (reported_usd is None)
    assert load_delegations(log) == [receipt]


@pytest.mark.parametrize("inline_below_spec_tokens", [0, 2000])
def test_hierarchy_keeps_individual_quotes_in_worker_inline_and_overhead_receipts(
    home: Path, inline_below_spec_tokens: int,
) -> None:
    from chimera.orchestration.artifacts import ArtifactStore
    from chimera.orchestration.envelope_verify import EnvelopeVerifier
    from chimera.orchestration.hierarchy import HierarchicalOrchestrator, HierarchyConfig
    from chimera.orchestration.receipts import load_delegations

    assert listing.remember_models((_quote_model(1.0),), home=home)

    class Backend:
        def __init__(self) -> None:
            self.calls = 0

        def planned_attempts(self, model: str, max_tokens: int | None) -> list[tuple[str, int]]:
            return [(model, 100)]

        def complete(self, messages: list[MessageLike], **kwargs: Any) -> CompletionResult:
            self.calls += 1
            if self.calls == 1:
                content = json.dumps([{"objective": "Summarize source A"}])
                assert listing.remember_models((_quote_model(10.0),), home=home)
            else:
                content = "Findings: everything nominal."
            return CompletionResult(
                content=content, model=STRICT_SLUG, prompt_tokens=50, completion_tokens=100,
                cache_read_tokens=20, cache_write_tokens=10,
            )

    budget = SpendBudget(max_usd=0.02, strict=True)
    backend = Backend()
    log = home / "delegations.jsonl"
    store = ArtifactStore(home / "artifacts")
    outcome = HierarchicalOrchestrator(
        SpendCappedBackend(backend, budget),
        weak_model=STRICT_SLUG, mid_model=STRICT_SLUG, top_model=STRICT_SLUG,
        store=store, verifier=EnvelopeVerifier(store=store, backend=None, spot_rate=0.0),
        receipts_path=log, config=HierarchyConfig(
            max_workers=1, inline_below_spec_tokens=inline_below_spec_tokens,
        ),
    ).run("Compare doc A and doc B")

    assert not outcome.fell_back
    assert backend.calls == 3
    [worker, decompose, synthesis] = outcome.receipts
    assert worker.tier == ("top" if inline_below_spec_tokens else "mid")
    assert [r.usd for r in outcome.receipts] == [0.00066, 0.000066, 0.00066]
    assert decompose.task_id == "decompose" and synthesis.task_id == "synthesis"
    assert all(r.usd_estimated for r in outcome.receipts)
    assert budget.spent == 0.001386
    assert load_delegations(log) == outcome.receipts


@pytest.mark.parametrize("as_completion", [True, False])
@pytest.mark.parametrize("retry_judge", [True, False])
def test_fusion_keeps_stage_costs_after_the_index_changes(
    home: Path, as_completion: bool, retry_judge: bool,
) -> None:
    from chimera.fusion.engine import FusionConfig, FusionEngine
    from chimera.fusion.receipts import receipt_from_trace

    assert listing.remember_models((_quote_model(1.0),), home=home)

    class Backend:
        def __init__(self) -> None:
            self.calls = 0

        def planned_attempts(self, model: str, max_tokens: int | None) -> list[tuple[str, int]]:
            return [(model, 100)]

        def complete(self, messages: list[MessageLike], **kwargs: Any) -> CompletionResult:
            self.calls += 1
            if self.calls == 1:
                assert listing.remember_models((_quote_model(10.0),), home=home)
            return CompletionResult(
                content="" if retry_judge and self.calls == 2 else "Answer",
                model=STRICT_SLUG, prompt_tokens=50, completion_tokens=100,
                cache_read_tokens=20, cache_write_tokens=10,
            )

    backend = Backend()
    budget = SpendBudget(max_usd=0.10, strict=True)
    engine = FusionEngine(
        SpendCappedBackend(backend, budget),
        FusionConfig(panel=[STRICT_SLUG], judge=STRICT_SLUG, synthesizer=STRICT_SLUG, max_workers=1),
    )
    messages: list[MessageLike] = [{"role": "user", "content": "Say hello"}]
    if as_completion:
        cost = price_completion(engine.complete(messages))
        assert cost.estimated and cost.unpriced is None
        total = cost.usd
    else:
        receipt = receipt_from_trace(engine.run(messages))
        assert [s.usd for s in receipt.stages] == [
            0.000066, (0.00132 if retry_judge else 0.00066), 0.00066,
        ]
        assert all(s.usd_estimated for s in receipt.stages)
        total = receipt.total_usd
    assert backend.calls == (4 if retry_judge else 3)
    assert total == budget.spent == (0.002046 if retry_judge else 0.001386)

@pytest.mark.parametrize(
    ("ending", "expected_costs"),
    [
        ("decompose_failed", [0.002, 0.001]),
        ("workers_failed", [0.001, 0.001, 0.001]),
        ("cancelled", [0.001]),
    ],
)
def test_hierarchy_keeps_spend_when_it_does_not_reach_synthesis(
    home: Path, ending: str, expected_costs: list[float],
) -> None:
    from chimera.orchestration.artifacts import ArtifactStore
    from chimera.orchestration.hierarchy import HierarchicalOrchestrator
    from chimera.orchestration.receipts import load_delegations

    set_price(STRICT_SLUG, ModelPrice(0.0, 1.0))

    class Backend:
        def __init__(self) -> None:
            self.calls = 0

        def planned_attempts(self, model: str, max_tokens: int | None) -> list[tuple[str, int]]:
            return [(model, 100)]

        def complete(self, messages: list[MessageLike], **kwargs: Any) -> CompletionResult:
            self.calls += 1
            if ending == "decompose_failed" and self.calls <= 2:
                content = "Not JSON"
            elif self.calls == 1:
                content = json.dumps([{"objective": "Compare sources"}])
            elif ending == "workers_failed" and self.calls == 2:
                content = ""
            else:
                content = "Fallback answer"
            return CompletionResult(
                content=content, model=STRICT_SLUG, prompt_tokens=10, completion_tokens=10, usd=0.001,
            )

    backend = Backend()
    budget = SpendBudget(max_usd=0.01, strict=True)
    log = home / "delegations.jsonl"
    outcome = HierarchicalOrchestrator(
        SpendCappedBackend(backend, budget),
        weak_model=STRICT_SLUG, mid_model=STRICT_SLUG, top_model=STRICT_SLUG,
        store=ArtifactStore(home / "artifacts"), receipts_path=log,
        should_stop=lambda: ending == "cancelled" and backend.calls > 0,
    ).run("Compare doc A and doc B")

    assert outcome.cancelled is (ending == "cancelled")
    assert outcome.fell_back is (ending != "cancelled")
    assert [receipt.usd for receipt in outcome.receipts] == expected_costs
    assert budget.spent == pytest.approx(sum(expected_costs))
    assert all(not receipt.usd_estimated for receipt in outcome.receipts)
    persisted = load_delegations(log)
    assert persisted == outcome.receipts
    assert sum(r.task_id == "decompose" for r in persisted) == 1
    assert outcome.total_tokens == 20 * backend.calls == sum(r.total_tokens for r in persisted)


@pytest.mark.parametrize("reported_usd", [None, 0.0042])
def test_hierarchy_receipt_includes_a_gateway_timeout_and_the_answer(
    home: Path, monkeypatch: pytest.MonkeyPatch, reported_usd: float | None,
) -> None:
    from types import SimpleNamespace

    # The first import can fetch LiteLLM's own model catalogue before the transport is patched.
    monkeypatch.setenv("LITELLM_LOCAL_MODEL_COST_MAP", "True")
    import litellm

    from chimera.orchestration.artifacts import ArtifactStore
    from chimera.orchestration.hierarchy import HierarchicalOrchestrator
    from chimera.orchestration.receipts import load_delegations, receipt_completion_cost

    fallback = f"{STRICT_SLUG}-fallback"
    for model in (STRICT_SLUG, fallback):
        set_price(model, ModelPrice(0.0, 0.28))

    attempts: list[str] = []

    def provider(**kwargs: Any) -> Any:
        model = str(kwargs["model"])
        attempts.append(model)
        assert kwargs["max_tokens"] == 10_000
        if model == STRICT_SLUG:
            raise TimeoutError("generation timed out")
        return SimpleNamespace(
            choices=[SimpleNamespace(
                message=SimpleNamespace(content="42", tool_calls=None), finish_reason="stop",
            )],
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=10_000),
        )

    class Gateway(LLMGateway):
        answer: CompletionResult

        def complete(self, messages: list[MessageLike], **kwargs: Any) -> CompletionResult:
            self.answer = super().complete(messages, **kwargs)
            self.answer.usd = reported_usd
            return self.answer

    monkeypatch.setattr(litellm, "completion", provider)
    settings = _settings(home).model_copy(update={
        "fallback_models": [fallback], "completion_ceiling": 10_000,
    })
    gateway = Gateway(settings)
    budget = SpendBudget(max_usd=0.006, strict=True)
    log = home / "delegations.jsonl"
    outcome = HierarchicalOrchestrator(
        SpendCappedBackend(gateway, budget),
        weak_model=STRICT_SLUG, mid_model=STRICT_SLUG, top_model=STRICT_SLUG,
        store=ArtifactStore(home / "artifacts"), receipts_path=log,
    ).run("What is 6 × 7?")

    assert outcome.fell_back and outcome.answer == "42"
    assert attempts == [STRICT_SLUG, fallback]
    assert gateway.answer.failed_attempts == [(STRICT_SLUG, 10_000)]
    assert gateway.answer.usd == reported_usd
    answered = 0.0028 if reported_usd is None else reported_usd
    assert price_completion(gateway.answer).usd == answered
    total = receipt_completion_cost(gateway.answer)
    [receipt] = outcome.receipts
    assert total.usd == receipt.usd == budget.spent == pytest.approx(answered + 0.0028)
    assert total.estimated and receipt.usd_estimated
    assert load_delegations(log) == [receipt]
