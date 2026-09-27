"""Choosing the System One model: what the list claims about each one, and what a save refuses.

The listing is OpenRouter's public index filtered to ``output_modalities: decisions``
(`chimera/decisions/system_one.py`). What is held here is how each entry is READ — no test reaches
the network: the fetcher is replaced, and a live index would test whichever models were published
today. Four ways the feature could lie, each with its tests below:

- **Offering a model the client cannot talk to.** The Decisions client sends the Jev shape (Noul,
  Choice, Score-as-Choice). A behaviour-scoring model, a moving alias, a family nobody classified —
  listed, and never choosable.
- **Calling a model calibrated because some map exists.** A map is keyed on the model; the local
  backend's map says nothing about Kev.
- **Reading a failed fetch as "there are none"** — or as licence to accept a slug nobody could check.
- **Saving a pair the factory cannot honour** — a Jev slug handed to Ollama halts every decision, and
  the halt reads as the band being down, not as the setting being wrong.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from chimera.api.config_api import APPLIES_WHEN, is_editable, patch_config, read_config
from chimera.api.schemas import ConfigOut, DecisionsCfgOut
from chimera.config import Settings, get_settings
from chimera.decisions import system_one
from chimera.decisions.calibration import CalibrationMaps, PlattMap
from chimera.decisions.system_one import check_choice, list_models

KEYS = ("CHIMERA_DECISION_BACKEND", "CHIMERA_DECISION_MODEL")


def _entry(slug: str, *, decisions: bool = True, **over: Any) -> dict[str, Any]:
    """One index entry, in the shape the endpoint returned on 2026-09-27."""
    entry: dict[str, Any] = {
        "id": slug,
        "name": f"Vendor: {slug}",
        "context_length": 32000,
        "pricing": {"prompt": "0.000000042", "completion": "0"},
        "architecture": {"output_modalities": ["decisions"] if decisions else ["text"]},
        "description": f"{slug} description",
    }
    entry.update(over)
    return entry


INDEX = [
    _entry("respan/span-01", context_length=0, pricing={"prompt": "0.00000002"}),
    _entry("respan/span-01-lite:free", context_length=0, pricing={"prompt": "0"}),
    _entry("jaredpalmer/kev-4b", context_length=8192),
    _entry("~typesafe/jev-latest"),
    _entry("typesafe/jev-1.13"),
    # A chat model the query parameter let through: the client-side filter is what keeps it out.
    _entry("openai/gpt-4o", decisions=False),
    # A decision model from a family nobody classified.
    _entry("newlab/oracle-1", pricing={"prompt": "-1"}),
]


class _Index:
    """The fetcher, replaced: answers ``INDEX`` (or a failure) and counts the calls."""

    def __init__(self, entries: list[dict[str, Any]] | None = None, reason: str = "") -> None:
        self.entries = INDEX if entries is None else entries
        self.reason = reason
        self.calls: list[dict[str, str] | None] = []

    def __call__(self, *, timeout_s: float = 6.0, params: dict[str, str] | None = None) -> Any:
        self.calls.append(params)
        return ([], self.reason) if self.reason else (list(self.entries), "")


@pytest.fixture(autouse=True)
def _fresh_cache() -> Any:
    system_one._cache = None
    yield
    system_one._cache = None


@pytest.fixture
def index(monkeypatch: pytest.MonkeyPatch) -> _Index:
    fake = _Index()
    monkeypatch.setattr(system_one, "fetch_openrouter_index", fake)
    return fake


@pytest.fixture
def own_keys(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """`patch_config` writes os.environ for real: own both names (absent), a private home and cwd."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("CHIMERA_SERVER_TOKEN", raising=False)
    for key in KEYS:
        monkeypatch.setenv(key, "")
        monkeypatch.delenv(key)
    get_settings.cache_clear()
    yield tmp_path
    get_settings.cache_clear()


def _by_slug(maps: CalibrationMaps | None = None) -> dict[str, system_one.SystemOneModel]:
    return {m.slug: m for m in list_models(maps).models}


# --- the listing -----------------------------------------------------------------------------------


def test_the_index_is_asked_for_decision_models_and_filtered_again_here(index: _Index) -> None:
    models = _by_slug()

    assert index.calls == [{"output_modalities": "decisions"}]
    assert "openai/gpt-4o" not in models  # the query parameter is the vendor's; the filter is ours
    assert set(models) == {e["id"] for e in INDEX} - {"openai/gpt-4o"}


def test_each_row_carries_price_context_and_description_without_inventing_any(index: _Index) -> None:
    models = _by_slug()

    assert models["jaredpalmer/kev-4b"].input_per_m == pytest.approx(0.042)
    assert models["jaredpalmer/kev-4b"].context == 8192
    assert models["typesafe/jev-1.13"].description == "typesafe/jev-1.13 description"
    assert models["respan/span-01"].context is None  # the index says 0: unknown, not zero tokens
    assert models["respan/span-01-lite:free"].input_per_m == 0.0  # genuinely free stays free
    assert models["newlab/oracle-1"].input_per_m is None  # "-1" is quoted at request time, not free


def test_only_the_jev_contract_is_choosable(index: _Index) -> None:
    models = _by_slug()

    assert models["typesafe/jev-1.13"].selectable
    assert models["typesafe/jev-1.13"].questions == ("noul", "choice", "score")
    assert models["jaredpalmer/kev-4b"].selectable
    assert models["jaredpalmer/kev-4b"].contract == "jev"

    span = models["respan/span-01"]
    assert (span.contract, span.questions, span.selectable, span.refusal) == (
        "behavior", ("noul",), False, "behavior_contract",
    )
    alias = models["~typesafe/jev-latest"]
    assert (alias.alias, alias.selectable, alias.refusal) == (True, False, "alias")
    unknown = models["newlab/oracle-1"]
    assert (unknown.contract, unknown.questions, unknown.selectable, unknown.refusal) == (
        "unknown", (), False, "unknown_contract",
    )


def test_no_row_is_calibrated_by_a_map_that_belongs_to_another_model(index: _Index) -> None:
    """The shipped map is the LOCAL backend's, on qwen3:4b: it calibrates nothing in this list."""
    assert not any(m.calibrated for m in _by_slug(CalibrationMaps.shipped()).values())


def test_a_map_for_one_slug_calibrates_that_slug_and_no_other(index: _Index) -> None:
    refit = PlattMap(
        id="x", decision="governance.danger", backend="openrouter_decisions", model="jaredpalmer/kev-4b",
        prompt_hash="abc", a=1.0, b=0.0, n=40, positives=12, fitted_at="2026-09-27", source="test",
    )
    maps = CalibrationMaps.shipped().merged(CalibrationMaps([refit]))

    calibrated = sorted(slug for slug, m in _by_slug(maps).items() if m.calibrated)
    assert calibrated == ["jaredpalmer/kev-4b"]


@pytest.mark.parametrize("reason", ["unreachable", "http_error", "unreadable"])
def test_offline_the_list_is_the_default_alone_and_says_so(
    monkeypatch: pytest.MonkeyPatch, reason: str
) -> None:
    monkeypatch.setattr(system_one, "fetch_openrouter_index", _Index(reason=reason))

    listing = list_models()

    assert listing.stale and listing.reason == reason
    assert [m.slug for m in listing.models] == ["typesafe/jev-1.13"]
    assert listing.models[0].selectable and listing.models[0].input_per_m is None


def test_an_index_with_no_decision_model_is_not_read_as_an_empty_family(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(system_one, "fetch_openrouter_index", _Index([_entry("openai/gpt-4o", decisions=False)]))

    listing = list_models()

    assert listing.stale and [m.slug for m in listing.models] == ["typesafe/jev-1.13"]


def test_the_listing_is_cached_and_a_failure_for_less_time(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = [1000.0]
    monkeypatch.setattr(system_one.time, "monotonic", lambda: clock[0])
    fake = _Index()
    monkeypatch.setattr(system_one, "fetch_openrouter_index", fake)

    list_models()
    clock[0] += system_one.CACHE_TTL_S - 1
    list_models()
    assert len(fake.calls) == 1
    clock[0] += 2
    list_models()
    assert len(fake.calls) == 2

    fake.reason = "unreachable"
    system_one._cache = None
    list_models()
    clock[0] += system_one.CACHE_FAILURE_TTL_S + 1
    list_models()
    assert len(fake.calls) == 4  # a failure is retried after a minute, not an hour


# --- the check ---------------------------------------------------------------------------------------


def test_a_jev_contract_model_or_the_default_is_accepted(index: _Index) -> None:
    listing = list_models()
    for model in ("", "typesafe/jev-1.13", "jaredpalmer/kev-4b"):
        check_choice("openrouter_decisions", model, listing)
    check_choice("local_logprob", "", listing)
    check_choice("local_logprob", "qwen3:8b", listing)  # an Ollama tag: not this list's to judge
    check_choice("hosted_verbalized", "openrouter/deepseek/deepseek-chat", listing)


@pytest.mark.parametrize(
    ("backend", "model", "says"),
    [
        ("telepathy", "", "unknown decision backend"),
        ("openrouter_decisions", "vendor/not-listed", "not in OpenRouter's list"),
        ("openrouter_decisions", "~typesafe/jev-latest", "moving alias"),
        ("openrouter_decisions", "respan/span-01", "behaviour-scoring"),
        ("openrouter_decisions", "newlab/oracle-1", "not verified"),
        ("local_logprob", "typesafe/jev-1.13", "is a System One model"),
        ("hosted_verbalized", "jaredpalmer/kev-4b", "is a System One model"),
    ],
)
def test_a_pair_the_factory_cannot_honour_is_refused_by_name(
    index: _Index, backend: str, model: str, says: str
) -> None:
    with pytest.raises(ValueError, match=says):
        check_choice(backend, model, list_models())


def test_offline_only_the_default_passes_and_the_refusal_says_why(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(system_one, "fetch_openrouter_index", _Index(reason="unreachable"))
    listing = list_models()

    check_choice("openrouter_decisions", "typesafe/jev-1.13", listing)
    with pytest.raises(ValueError, match="could not be reached"):
        check_choice("openrouter_decisions", "jaredpalmer/kev-4b", listing)


# --- the settings surface ----------------------------------------------------------------------------


def test_the_screen_may_write_both_keys_and_a_chat_waits_for_the_next_one() -> None:
    for key in KEYS:
        assert is_editable(key)
        assert APPLIES_WHEN[key] == "next_conversation"


def test_the_config_reports_the_pair_and_a_server_without_it_reads_as_the_default(tmp_path: Path) -> None:
    on = Settings(  # type: ignore[call-arg]
        CHIMERA_HOME=str(tmp_path), CHIMERA_DECISION_BACKEND="openrouter_decisions",
        CHIMERA_DECISION_MODEL="jaredpalmer/kev-4b",
    )
    # The grounded-answer switch travels in the same block (study 26): the card that picks the
    # instrument is the card that says what it checks.
    assert read_config(on)["decisions"] == {
        "backend": "openrouter_decisions", "model": "jaredpalmer/kev-4b",
        "verified_answers": True, "verified_answers_threshold": 0.8,
    }
    assert "decisions" in ConfigOut.model_fields
    assert DecisionsCfgOut().model_dump() == {
        "backend": "local_logprob", "model": "", "verified_answers": True, "verified_answers_threshold": 0.8,
    }


def test_a_valid_pair_is_written_and_read_back(index: _Index, own_keys: Path) -> None:
    env_file = own_keys / ".env"

    patch_config({KEYS[0]: "openrouter_decisions", KEYS[1]: "jaredpalmer/kev-4b"}, env_path=env_file)

    assert env_file.read_text(encoding="utf-8").splitlines() == [
        "CHIMERA_DECISION_BACKEND=openrouter_decisions", "CHIMERA_DECISION_MODEL=jaredpalmer/kev-4b",
    ]
    settings = get_settings()
    assert (settings.decision_backend, settings.decision_model) == ("openrouter_decisions", "jaredpalmer/kev-4b")


def test_a_refused_pair_writes_nothing(index: _Index, own_keys: Path) -> None:
    env_file = own_keys / ".env"
    env_file.write_text("CHIMERA_DEFAULT_MODEL=some/model\n", encoding="utf-8")

    with pytest.raises(ValueError, match="behaviour-scoring"):
        patch_config({KEYS[0]: "openrouter_decisions", KEYS[1]: "respan/span-01"}, env_path=env_file)

    assert env_file.read_text(encoding="utf-8") == "CHIMERA_DEFAULT_MODEL=some/model\n"
    assert get_settings().decision_backend == "local_logprob"


def test_a_patch_naming_one_key_is_checked_against_the_other_as_it_stands(
    index: _Index, own_keys: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Switching only the backend back to local while a Jev slug is saved would hand it to Ollama."""
    monkeypatch.setenv(KEYS[0], "openrouter_decisions")
    monkeypatch.setenv(KEYS[1], "typesafe/jev-1.13")
    get_settings.cache_clear()

    with pytest.raises(ValueError, match="is a System One model"):
        patch_config({KEYS[0]: "local_logprob"}, env_path=own_keys / ".env")
    assert not (own_keys / ".env").exists()


def test_an_empty_model_never_reaches_the_network(monkeypatch: pytest.MonkeyPatch, own_keys: Path) -> None:
    def refuse(**_: Any) -> Any:
        raise AssertionError("an empty model is the backend's default; nothing to look up")

    monkeypatch.setattr(system_one, "fetch_openrouter_index", refuse)
    patch_config({KEYS[0]: "hosted_verbalized", KEYS[1]: ""}, env_path=own_keys / ".env")
    assert get_settings().decision_backend == "hosted_verbalized"


# --- the endpoint and the terminal ---------------------------------------------------------------------


def _client() -> TestClient:
    from typing import cast

    from chimera.api import build_api_app
    from chimera.interface import ChatSession
    from chimera.interface.session import SupportsRun

    return TestClient(build_api_app(lambda: ChatSession(cast(SupportsRun, None))))


def test_the_endpoint_lists_the_models_and_the_active_pair(index: _Index, own_keys: Path) -> None:
    body = _client().get("/api/decisions/models").json()

    assert (body["backend"], body["model"], body["default_model"]) == ("local_logprob", "", "qwen3:4b")
    assert body["backends"] == ["local_logprob", "hosted_verbalized", "openrouter_decisions"]
    assert body["stale"] is False and body["openrouter_key_set"] is False
    rows = {m["slug"]: m for m in body["models"]}
    assert rows["jaredpalmer/kev-4b"]["selectable"] is True
    assert rows["respan/span-01"]["refusal"] == "behavior_contract"
    assert all(m["calibrated"] is False for m in body["models"])


def test_the_endpoint_saves_through_the_config_patch_and_refuses_with_a_400(index: _Index, own_keys: Path) -> None:
    client = _client()

    bad = client.patch("/api/config", json={KEYS[0]: "openrouter_decisions", KEYS[1]: "~typesafe/jev-latest"})
    assert bad.status_code == 400 and "moving alias" in bad.json()["detail"]
    good = client.patch("/api/config", json={KEYS[0]: "openrouter_decisions", KEYS[1]: "jaredpalmer/kev-4b"})
    assert good.status_code == 200
    body = client.get("/api/decisions/models").json()
    assert (body["backend"], body["model"]) == ("openrouter_decisions", "jaredpalmer/kev-4b")


def test_the_terminal_lists_marks_the_active_one_and_the_calibrated_column(index: _Index, own_keys: Path) -> None:
    from chimera.cli.main import app

    result = CliRunner().invoke(app, ["decisions", "models"], env={"COLUMNS": "200"})

    assert result.exit_code == 0, result.output
    assert "jaredpalmer/kev-4b" in result.output and "respan/span-01" in result.output
    assert "no (behavior contract)" in result.output
    jev_row = next(line for line in result.output.splitlines() if "typesafe/jev-1.13" in line)
    assert "*" not in jev_row  # the local backend is active: no System One row is
    assert "active: local_logprob / qwen3:4b (default)" in result.output


def test_the_terminal_choice_writes_both_keys_and_refuses_what_the_screen_refuses(
    index: _Index, own_keys: Path
) -> None:
    from chimera.cli.main import app

    runner = CliRunner()
    refused = runner.invoke(app, ["decisions", "use", "openrouter_decisions", "respan/span-01"])
    assert refused.exit_code == 1 and "refused" in refused.output
    assert not (own_keys / ".env").exists()

    chosen = runner.invoke(app, ["decisions", "use", "openrouter_decisions", "jaredpalmer/kev-4b"])
    assert chosen.exit_code == 0, chosen.output
    assert "no OpenRouter key is set" in chosen.output
    listed = runner.invoke(app, ["decisions", "models"], env={"COLUMNS": "200"})
    kev_row = next(line for line in listed.output.splitlines() if "jaredpalmer/kev-4b" in line)
    assert "*" in kev_row

    back = runner.invoke(app, ["decisions", "use", "local_logprob"])
    assert back.exit_code == 0, back.output
    assert (own_keys / ".env").read_text(encoding="utf-8").splitlines() == [
        "CHIMERA_DECISION_BACKEND=local_logprob", "CHIMERA_DECISION_MODEL=",
    ]


def test_the_decide_endpoint_rebuilds_its_decider_when_the_pair_changes(
    index: _Index, own_keys: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Kept for the process's life, a choice saved in Settings would answer `POST /api/decide` only
    after a relaunch — while the screen says the next call reads it."""
    import chimera.decisions.factory as factory
    import chimera.decisions.interface as interface

    built: list[tuple[str, str]] = []

    def fake_build(settings: Any, **_: Any) -> Any:
        built.append((settings.decision_backend, settings.decision_model))
        return object()

    monkeypatch.setattr(factory, "build_decider", fake_build)
    monkeypatch.setattr(interface, "decide", lambda d, req: {"model": "m", "answers": {}, "receipts": {}})
    client = _client()
    ask = {"state": "s", "questions": {"q": {"type": "noul", "instructions": "i", "criteria": {"yes": "y"}}}}

    assert client.post("/api/decide", json=ask).status_code == 200
    assert client.post("/api/decide", json=ask).status_code == 200
    assert built == [("local_logprob", "")]
    client.patch("/api/config", json={KEYS[0]: "openrouter_decisions", KEYS[1]: "jaredpalmer/kev-4b"})
    client.post("/api/decide", json=ask)
    assert built == [("local_logprob", ""), ("openrouter_decisions", "jaredpalmer/kev-4b")]


def test_the_shared_fetcher_sends_the_query_and_never_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """One fetcher for both lists (`chimera/providers/listing.py`): the query string reaches the
    request, and no network is an answer, not an exception."""
    import httpx

    from chimera.providers.listing import OPENROUTER_MODELS_URL, fetch_openrouter_index

    seen: dict[str, Any] = {}

    class _Ok:
        status_code = 200

        def json(self) -> Any:
            return {"data": [_entry("typesafe/jev-1.13"), "not an entry"]}

    def fake_get(url: str, **kwargs: Any) -> Any:
        seen.update(url=url, **kwargs)
        return _Ok()

    monkeypatch.setattr(httpx, "get", fake_get)
    entries, reason = fetch_openrouter_index(params={"output_modalities": "decisions"})
    assert (seen["url"], seen["params"], reason) == (OPENROUTER_MODELS_URL, {"output_modalities": "decisions"}, "")
    assert [e["id"] for e in entries] == ["typesafe/jev-1.13"]

    def down(url: str, **kwargs: Any) -> Any:
        raise httpx.ConnectError("no network")

    monkeypatch.setattr(httpx, "get", down)
    assert fetch_openrouter_index(params={"output_modalities": "decisions"}) == ([], "unreachable")
