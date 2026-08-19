"""The list of models a request may name, and everything it refuses to make up.

Until this existed, naming a model was a memory test: a free-text box in Settings, a free-text box
in the wizard, and no way at all to name one from the conversation. The fix is a list — and a list
is exactly the kind of feature that lies quietly, so the assertions here are mostly about what does
NOT appear in it.

Three failures, each with its own test below:

- **Offering models the user's keys cannot call.** OpenRouter's index is public, so the easy version
  of this feature shows four hundred slugs to someone holding an Anthropic key. Every one of them
  answers 401, after they picked it.
- **Turning an unknown into a number.** OpenRouter quotes some models at request time and marks them
  ``"-1"``. Read as a float that is zero, or as a price at all, it becomes a free model in the menu
  and a division in a spend ceiling.
- **Reading absence as evidence.** A fetch that failed and a catalogue that is genuinely empty
  arrive at the UI identically unless the reason travels with them — and the curated list, which
  needs no network, has to survive a failure rather than be deleted by it.

Nothing here reaches the network: `httpx.get` is replaced. What is under test is how each answer is
READ, and a live catalogue would test whichever models happened to be published today.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from chimera.providers import listing
from chimera.providers.listing import ModelOption, available_models, openrouter_models


class _Settings:
    """The slice of Settings the listing needs, duck-typed like the tier resolver's."""

    def __init__(self, providers: list[str], *, ollama: str = "", default: str = "vendor/model"):
        self._providers = providers
        self.ollama_base_url = ollama
        self.default_model = default

    def configured_providers(self) -> list[str]:
        return list(self._providers)


class _Response:
    def __init__(self, status_code: int, payload: Any = None, *, valid_json: bool = True) -> None:
        self.status_code = status_code
        self._payload = payload
        self._valid_json = valid_json

    def json(self) -> Any:
        if not self._valid_json:
            raise ValueError("not json")
        return self._payload


def _entry(model_id: str, **over: Any) -> dict[str, Any]:
    """One OpenRouter index entry, in the shape their endpoint actually returns."""
    entry = {
        "id": model_id,
        "name": f"Vendor: {model_id}",
        "context_length": 128_000,
        "pricing": {"prompt": "0.00000025", "completion": "0.00000095"},
        "architecture": {"input_modalities": ["text"]},
        "supported_parameters": ["tools", "temperature"],
    }
    entry.update(over)
    return entry


def _answer(monkeypatch: pytest.MonkeyPatch, response: _Response | Exception) -> None:
    def fake_get(url: str, **kwargs: Any) -> _Response:
        assert url == listing.OPENROUTER_MODELS_URL, f"asked {url}, not the model index"
        if isinstance(response, Exception):
            raise response
        return response

    monkeypatch.setattr(httpx, "get", fake_get)


@pytest.fixture(autouse=True)
def _no_cache_between_tests() -> Any:
    """The fetch is cached for an hour in production, which would make these tests read each other's
    answers. Cleared around every test rather than at the end of one, so a failure cannot leak."""
    listing._cache = None
    yield
    listing._cache = None


def test_the_index_is_read_into_options(monkeypatch: pytest.MonkeyPatch) -> None:
    _answer(monkeypatch, _Response(200, {"data": [_entry("deepseek/deepseek-chat-v3.1")]}))

    models, reason = openrouter_models()

    assert reason == ""
    assert len(models) == 1
    found = models[0]
    # Prefixed, because LiteLLM routes on the first segment and the UI must never assemble a slug.
    assert found.slug == "openrouter/deepseek/deepseek-chat-v3.1"
    assert found.tools is True
    # Per-token decimals become dollars per million, which is the only unit anyone reads.
    assert found.input_per_m == 0.25
    assert found.output_per_m == 0.95
    assert found.context_k == 128


def test_a_price_quoted_at_request_time_is_unknown_not_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    # `-1` is OpenRouter's marker for "quoted per request". Zero would put "free" in the menu next
    # to a model that bills, and it is the number a spend ceiling would divide by.
    _answer(
        monkeypatch,
        _Response(200, {"data": [_entry("vendor/variable", pricing={"prompt": "-1", "completion": "-1"})]}),
    )

    models, _ = openrouter_models()

    assert models[0].input_per_m is None
    assert models[0].output_per_m is None
    assert models[0].free is False


def test_a_model_without_tool_support_says_so_and_one_that_is_silent_does_not(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # False and None are different answers: a coding turn on a model that cannot call tools produces
    # a description of an edit that never happened, and that warning is only worth showing when it
    # is not a guess.
    _answer(
        monkeypatch,
        _Response(
            200,
            {
                "data": [
                    _entry("vendor/no-tools", supported_parameters=["temperature"]),
                    _entry("vendor/unsaid", supported_parameters=None),
                ]
            },
        ),
    )

    models, _ = openrouter_models()

    assert models[0].tools is False
    assert models[1].tools is None


def test_free_tiers_are_marked(monkeypatch: pytest.MonkeyPatch) -> None:
    _answer(
        monkeypatch,
        _Response(200, {"data": [_entry("vendor/model:free", pricing={"prompt": "0", "completion": "0"})]}),
    )

    assert openrouter_models()[0][0].free is True


def test_vision_comes_from_the_modalities_not_from_the_vendor(monkeypatch: pytest.MonkeyPatch) -> None:
    _answer(
        monkeypatch,
        _Response(
            200,
            {
                "data": [
                    _entry("vendor/sees", architecture={"input_modalities": ["text", "image"]}),
                    _entry("vendor/blind"),
                ]
            },
        ),
    )

    models, _ = openrouter_models()
    assert models[0].vision is True
    assert models[1].vision is False


@pytest.mark.parametrize(
    ("response", "reason"),
    [
        (httpx.ConnectError("no route"), "unreachable"),
        (_Response(500), "http_error"),
        (_Response(200, valid_json=False), "unreadable"),
        (_Response(200, {"data": "not a list"}), "unreadable"),
    ],
)
def test_every_failure_is_a_token_never_an_exception(
    monkeypatch: pytest.MonkeyPatch, response: Any, reason: str
) -> None:
    # This populates a menu. An exception here would turn "the catalogue is having a bad day" into a
    # 500 that reads as a bug in Chimera, and the reason is a WORD because the app ships ten
    # languages and English prose from the server is the one line the user cannot read.
    _answer(monkeypatch, response)

    models, got = openrouter_models()

    assert models == ()
    assert got == reason


def test_the_fetch_is_cached_rather_than_repeated(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}

    def counting_get(url: str, **kwargs: Any) -> _Response:
        calls["n"] += 1
        return _Response(200, {"data": [_entry("vendor/model")]})

    monkeypatch.setattr(httpx, "get", counting_get)

    openrouter_models()
    openrouter_models()

    assert calls["n"] == 1


def test_a_remote_catalogue_is_not_offered_without_a_key(monkeypatch: pytest.MonkeyPatch) -> None:
    # The failure this prevents: four hundred OpenRouter slugs offered to someone holding only an
    # Anthropic key, every one of which answers 401 *after* they picked it.
    def forbidden(url: str, **kwargs: Any) -> _Response:
        raise AssertionError("asked OpenRouter without an OpenRouter key")

    monkeypatch.setattr(httpx, "get", forbidden)

    found = available_models(_Settings(["anthropic"]))

    assert found.reason == "no_provider"
    assert all(m.source != "openrouter" for m in found.models)


def test_the_wizard_may_ask_about_a_key_it_has_not_saved_yet(monkeypatch: pytest.MonkeyPatch) -> None:
    # The one moment where filtering by configured providers answers the wrong question: the user is
    # holding the key they are about to paste, and "what does this buy" is exactly what they asked.
    _answer(monkeypatch, _Response(200, {"data": [_entry("vendor/model")]}))

    found = available_models(_Settings([]), provider="openrouter")

    assert found.reason == ""
    assert any(m.source == "openrouter" for m in found.models)


def test_a_failed_fetch_leaves_the_curated_list_standing(monkeypatch: pytest.MonkeyPatch) -> None:
    # An empty menu says "your key buys nothing", which is a claim about the user's account that a
    # network failure does not support. The curated models need no network and are still callable.
    _answer(monkeypatch, httpx.ConnectError("down"))

    found = available_models(_Settings(["openrouter"]))

    assert found.reason == "unreachable"
    assert found.models, "a failed remote fetch deleted the offline catalogue"
    assert all(m.recommended for m in found.models)


def test_a_curated_slug_the_live_index_dropped_is_not_offered(monkeypatch: pytest.MonkeyPatch) -> None:
    # The catalogue says of itself that its slugs go stale. Offering a retired one is a 404 on the
    # first call, after the user chose it — and the live index is the only thing that can tell.
    from chimera.providers.catalog import CATALOG

    alive = next(e.slug for e in CATALOG if e.slug.startswith("openrouter/"))
    _answer(monkeypatch, _Response(200, {"data": [_entry(alive.removeprefix("openrouter/"))]}))

    found = available_models(_Settings(["openrouter"]))
    slugs = {m.slug for m in found.models}

    assert alive in slugs
    retired = {e.slug for e in CATALOG if e.slug.startswith("openrouter/")} - {alive}
    assert not (retired & slugs), "a slug the live index no longer carries is still on offer"


def test_the_live_entry_wins_on_price_and_keeps_the_recommendation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A model in both catalogues is ONE row: the live price (the curated numbers are documented as
    # approximate and stale) with the recommendation that made us list it in the first place.
    from chimera.providers.catalog import CATALOG

    curated = next(e for e in CATALOG if e.slug.startswith("openrouter/") and e.input_per_m)
    _answer(
        monkeypatch,
        _Response(
            200,
            {
                "data": [
                    _entry(
                        curated.slug.removeprefix("openrouter/"),
                        pricing={"prompt": "0.00001", "completion": "0.00002"},
                    )
                ]
            },
        ),
    )

    found = available_models(_Settings(["openrouter"]))
    merged = next(m for m in found.models if m.slug == curated.slug)

    assert merged.recommended is True
    assert merged.input_per_m == 10.0
    assert merged.input_per_m != curated.input_per_m


def test_recommended_models_come_first(monkeypatch: pytest.MonkeyPatch) -> None:
    from chimera.providers.catalog import CATALOG

    curated = next(e.slug for e in CATALOG if e.slug.startswith("openrouter/"))
    _answer(
        monkeypatch,
        _Response(
            200,
            {"data": [_entry("vendor/unrelated"), _entry(curated.removeprefix("openrouter/"))]},
        ),
    )

    models = available_models(_Settings(["openrouter"])).models

    assert models[0].slug == curated
    assert models[0].recommended is True


def test_local_models_are_listed_and_priced_at_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    # The one price we can state without asking anyone: a model running on this machine bills
    # nothing per token.
    monkeypatch.setattr(
        listing,
        "_ollama_options",
        lambda base: [ModelOption(slug="ollama/llama3", label="llama3", vendor="Ollama", source="ollama", free=True)],
    )
    _answer(monkeypatch, _Response(200, {"data": []}))

    models = available_models(_Settings(["openrouter"], ollama="http://localhost:11434")).models

    assert any(m.slug == "ollama/llama3" and m.free for m in models)


def test_the_endpoint_reports_the_default_next_to_the_list(monkeypatch: pytest.MonkeyPatch) -> None:
    """The picker marks the default rather than guessing it, so the response has to carry it.

    Without this field the UI knows what you can pick and not what happens if you pick nothing —
    and "nothing chosen" is the state every conversation starts in.
    """
    from fastapi.testclient import TestClient

    from chimera.api import build_api_app
    from chimera.interface import ChatSession

    _answer(monkeypatch, _Response(200, {"data": [_entry("vendor/model")]}))
    client = TestClient(build_api_app(lambda: ChatSession(None)))  # type: ignore[arg-type]

    body = client.get("/api/models").json()

    assert body["default"], "the response does not say what runs when nothing is picked"
    assert isinstance(body["models"], list)
    assert body["reason"] in {"", "no_provider", "unreachable", "http_error", "unreadable"}


# --- The price side ------------------------------------------------------------------------------
#
# Everything below is about one sentence a user reads: "price unknown", under a turn they just paid
# for. It appeared because the receipt priced models from a hand-written table of ~20 families, and
# the product's own default was not one of them. The fix is not "add the default to the table" — the
# table would be wrong again on the next release — it is to remember what the provider publishes.


def test_a_fetched_listing_leaves_the_prices_on_disk(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path))
    _answer(monkeypatch, _Response(200, {"data": [_entry("vendor/model")]}))

    available_models(_Settings(["openrouter"]))

    cache = tmp_path / listing.PRICE_CACHE_NAME
    assert cache.exists(), "the fetched index did not leave anything behind"
    saved = json.loads(cache.read_text(encoding="utf-8"))
    kept = saved["models"]["openrouter/vendor/model"]
    assert (kept["in"], kept["out"]) == (0.25, 0.95)
    # Capabilities ride along, and that is the point of keeping the file at all: the provider knows
    # which models accept images, and LiteLLM's table was wrong about that in both directions.
    assert kept["tools"] is True
    assert kept["vision"] is False


def test_a_model_quoted_per_request_is_not_written_as_a_price(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    # The one entry that must NOT reach the cache. A `-1` written as `0` would make a billed model
    # read as free in the receipt, which is worse than the "unknown" it replaces.
    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path))
    # Two models, so the file exists to be inspected: a listing where NOTHING is priceable writes
    # nothing at all, which is correct and would make this assertion vacuous.
    _answer(
        monkeypatch,
        _Response(
            200,
            {
                "data": [
                    _entry("vendor/variable", pricing={"prompt": "-1", "completion": "-1"}),
                    _entry("vendor/priced"),
                ]
            },
        ),
    )

    available_models(_Settings(["openrouter"]))

    saved = json.loads((tmp_path / listing.PRICE_CACHE_NAME).read_text(encoding="utf-8"))
    assert saved["models"]["openrouter/vendor/priced"]["in"] == 0.25
    # The row is kept — its capabilities are still worth knowing — but the price stays null rather
    # than becoming a zero, and nothing downstream may read it as one.
    assert saved["models"]["openrouter/vendor/variable"]["in"] is None
    assert listing.known_price("openrouter/vendor/variable") is None


def test_the_price_is_read_back_for_that_exact_slug_only(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    # Exact, never substring. Four hundred substring patterns is how `gpt-5.5` starts pricing
    # `gpt-5.5-mini` — the failure the existing family table already has and this must not add to.
    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path))
    _answer(monkeypatch, _Response(200, {"data": [_entry("vendor/model")]}))
    available_models(_Settings(["openrouter"]))

    assert listing.known_price("openrouter/vendor/model") == (0.25, 0.95)
    assert listing.known_price("openrouter/vendor/model-mini") is None
    assert listing.known_price("vendor/model") is None
    # Same rule for the capability, which is the one that decides whether an image is sent.
    assert listing.known_vision("openrouter/vendor/model") is False
    assert listing.known_vision("openrouter/vendor/model-mini") is None


def test_no_cache_is_an_absent_price_not_a_crash(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    # The state every install starts in, and the state of any machine that has never reached the
    # index. The caller falls back to its own table, exactly as before this existed.
    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path / "nothing-here"))
    assert listing.known_price("openrouter/vendor/model") is None


def test_the_receipt_prices_a_model_the_static_table_never_heard_of(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """The end this whole thing is for: a turn that used to say "price unknown" says a number.

    `vendor/model` is deliberately not in `_PRICES` — matching one of those families would prove the
    old path works, not the new one.
    """
    from chimera.fusion.receipts import resolve_price

    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path))
    assert resolve_price("openrouter/vendor/model") is None  # before

    _answer(monkeypatch, _Response(200, {"data": [_entry("vendor/model")]}))
    available_models(_Settings(["openrouter"]))

    priced = resolve_price("openrouter/vendor/model")  # after
    assert priced is not None
    assert (priced.input_per_m, priced.output_per_m) == (0.25, 0.95)


def test_an_exact_hand_set_price_still_beats_the_published_one(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """`set_price` documents itself as "checked first", and somebody who names one model means it —
    a negotiated rate, a proxy, a provider whose public number is not what they pay."""
    from chimera.fusion.receipts import ModelPrice, resolve_price, set_price

    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path))
    _answer(monkeypatch, _Response(200, {"data": [_entry("vendor/negotiated")]}))
    available_models(_Settings(["openrouter"]))

    set_price("openrouter/vendor/negotiated", ModelPrice(0.01, 0.02))

    assert resolve_price("openrouter/vendor/negotiated") == ModelPrice(0.01, 0.02)


def test_the_warm_up_stays_home_when_openrouter_is_not_configured(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    # An app that phones a third party for no benefit to this user is doing it for itself.
    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path))

    def forbidden(url: str, **kwargs: Any) -> _Response:
        raise AssertionError("warmed the price cache from OpenRouter without an OpenRouter key")

    monkeypatch.setattr(httpx, "get", forbidden)
    listing.warm_price_cache(_Settings(["anthropic"]))

    assert not (tmp_path / listing.PRICE_CACHE_NAME).exists()
