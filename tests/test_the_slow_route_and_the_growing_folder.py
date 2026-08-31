"""Two costs measured on a real install, neither of them a wrong answer.

* ``/api/models`` was the only slow route in the app — **2,136 ms** for a 94 KB network round trip,
  paid again on every open of the Settings screen, uncached.
* ``discarded/`` grew by one file per reverted attempt with no ceiling of any kind: 3 files in one
  session, 7 eighteen hours later, forever. Small today, and the shape nobody notices until it is
  a problem.

Free: no model call, no network.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from chimera.core.autonomous import (
    _DISCARDED_MAX_BYTES,
    _DISCARDED_MAX_FILES,
    _podar_descartados,
)

# --- the folder that had no ceiling ---------------------------------------------------------------


def _diffs(pasta: Path, quantos: int, *, tamanho: int = 100) -> list[Path]:
    """``quantos`` diffs, oldest first — the mtimes are set explicitly rather than left to the
    filesystem, because a test that writes them in a loop can produce identical timestamps and
    then "oldest first" would be whatever order the directory happens to return."""
    pasta.mkdir(parents=True, exist_ok=True)
    feitos = []
    for i in range(quantos):
        f = pasta / f"run{i:04d}-1.diff"
        f.write_text("x" * tamanho, encoding="utf-8")
        import os

        os.utime(f, (1_000_000 + i, 1_000_000 + i))
        feitos.append(f)
    return feitos


def test_a_folder_under_the_cap_is_left_alone(tmp_path: Path) -> None:
    """Pruning must be invisible to anyone who has not hit the bound."""
    _diffs(tmp_path, 5)

    assert _podar_descartados(tmp_path) == 0
    assert len(list(tmp_path.glob("*.diff"))) == 5


def test_the_oldest_go_first(tmp_path: Path) -> None:
    """Recovering work you were just doing is the whole point; a revert from last month is not
    something anyone comes back for."""
    _diffs(tmp_path, _DISCARDED_MAX_FILES + 3)

    _podar_descartados(tmp_path)

    restantes = {f.name for f in tmp_path.glob("*.diff")}
    assert len(restantes) == _DISCARDED_MAX_FILES
    assert "run0000-1.diff" not in restantes, "the oldest survived"
    assert f"run{_DISCARDED_MAX_FILES + 2:04d}-1.diff" in restantes, "the newest was deleted"


def test_size_binds_even_when_the_count_does_not(tmp_path: Path) -> None:
    """Both axes, because either alone can be defeated: a handful of enormous diffs fills a disk
    while the file count sits comfortably under its bound."""
    grande = _DISCARDED_MAX_BYTES // 2
    _diffs(tmp_path, 3, tamanho=grande)

    _podar_descartados(tmp_path)

    total = sum(f.stat().st_size for f in tmp_path.glob("*.diff"))
    assert len(list(tmp_path.glob("*.diff"))) < 3, "the count was under its cap so nothing pruned"
    assert total <= _DISCARDED_MAX_BYTES


def test_count_binds_even_when_the_size_does_not(tmp_path: Path) -> None:
    """The mirror: thousands of tiny files are nowhere near the byte cap and still fill a folder."""
    _diffs(tmp_path, _DISCARDED_MAX_FILES + 10, tamanho=10)

    _podar_descartados(tmp_path)

    assert len(list(tmp_path.glob("*.diff"))) == _DISCARDED_MAX_FILES


def test_it_only_touches_diffs(tmp_path: Path) -> None:
    """The folder is ours, and a pruner that deletes by position rather than by pattern is one
    stray file away from removing something it was never told about."""
    import os

    _diffs(tmp_path, _DISCARDED_MAX_FILES + 5)
    estranho = tmp_path / "LEIA-ME.txt"
    estranho.write_text("nao me apague", encoding="utf-8")
    # OLDER than every diff, deliberately. Written last it would be the newest and would survive a
    # pruner that deleted by position rather than by pattern — measured: that sabotage walked
    # straight through the first version of this test.
    os.utime(estranho, (1, 1))

    _podar_descartados(tmp_path)

    assert estranho.exists(), "the pruner deleted a file it was never told about"


def test_a_missing_folder_is_not_an_error(tmp_path: Path) -> None:
    """Best-effort throughout: failing to prune must never be the reason a revert fails."""
    assert _podar_descartados(tmp_path / "nunca-existiu") == 0


def test_the_writer_prunes_after_writing() -> None:
    """A pruner nothing calls is a folder with no ceiling. The call sits after the write, so a
    failure to prune cannot cost the file that was just saved."""
    import inspect

    from chimera.core.autonomous import AutonomousAgent

    fonte = inspect.getsource(AutonomousAgent._preserve_discarded)

    assert "_podar_descartados(destino.parent)" in fonte
    assert fonte.index("destino.write_text") < fonte.index("_podar_descartados")


# --- the only slow route in the app ---------------------------------------------------------------


def _client(tmp_path: Path) -> Any:
    from tests.test_api import _client as build

    return build(tmp_path)


def test_the_model_list_is_fetched_once_and_reused(tmp_path: Path, monkeypatch: Any) -> None:
    """Measured: 2,136 ms per call, on every open of the Settings screen."""
    chamadas = {"n": 0}
    import chimera.providers.listing as listing_mod

    class _Listing:
        models: list[Any] = []
        sources: list[str] = ["catalog"]
        reason = ""

    def _fake(settings: Any, *, provider: str | None = None, **kw: Any) -> Any:
        chamadas["n"] += 1
        return _Listing()

    monkeypatch.setattr(listing_mod, "available_models", _fake)
    client = _client(tmp_path)

    client.get("/api/models")
    client.get("/api/models")
    client.get("/api/models")

    assert chamadas["n"] == 1, f"the catalogue was fetched {chamadas['n']} times for three opens"


def test_a_different_provider_is_a_different_answer(tmp_path: Path, monkeypatch: Any) -> None:
    """``?provider=`` is the onboarding wizard asking *what does this key buy*. Serving it the
    previous provider's list is the one thing that question cannot survive — four different values
    returning byte-identical bodies is a defect this endpoint has already had once."""
    vistos: list[str | None] = []
    import chimera.providers.listing as listing_mod

    class _Listing:
        models: list[Any] = []
        sources: list[str] = []
        reason = ""

    def _fake(settings: Any, *, provider: str | None = None, **kw: Any) -> Any:
        vistos.append(provider)
        return _Listing()

    monkeypatch.setattr(listing_mod, "available_models", _fake)
    client = _client(tmp_path)

    client.get("/api/models")
    client.get("/api/models?provider=anthropic")
    client.get("/api/models?provider=openai")

    assert vistos == [None, "anthropic", "openai"]


def test_an_expired_entry_is_fetched_again(tmp_path: Path, monkeypatch: Any) -> None:
    """A cache with no expiry is a snapshot. The clock is moved rather than waited on."""
    chamadas = {"n": 0}
    import chimera.api.app as app_mod
    import chimera.providers.listing as listing_mod

    class _Listing:
        models: list[Any] = []
        sources: list[str] = []
        reason = ""

    def _fake(settings: Any, *, provider: str | None = None, **kw: Any) -> Any:
        chamadas["n"] += 1
        return _Listing()

    monkeypatch.setattr(listing_mod, "available_models", _fake)
    relogio = {"t": 1000.0}
    monkeypatch.setattr(app_mod.time, "monotonic", lambda: relogio["t"])
    client = _client(tmp_path)

    client.get("/api/models")
    relogio["t"] += 10_000  # far past any sane TTL
    client.get("/api/models")

    assert chamadas["n"] == 2


def test_two_apps_do_not_share_a_catalogue(tmp_path: Path, monkeypatch: Any) -> None:
    """Per app instance, not a module global: one test's fixture must not become another's answer,
    and two installs pointed at different providers must not read each other's list."""
    chamadas = {"n": 0}
    import chimera.providers.listing as listing_mod

    class _Listing:
        models: list[Any] = []
        sources: list[str] = []
        reason = ""

    def _fake(settings: Any, *, provider: str | None = None, **kw: Any) -> Any:
        chamadas["n"] += 1
        return _Listing()

    monkeypatch.setattr(listing_mod, "available_models", _fake)

    _client(tmp_path / "a").get("/api/models")
    _client(tmp_path / "b").get("/api/models")

    assert chamadas["n"] == 2


@pytest.mark.parametrize("campo", ["default", "models", "sources", "reason"])
def test_the_cached_answer_is_the_whole_answer(tmp_path: Path, monkeypatch: Any, campo: str) -> None:
    """A cache that drops a field turns a fast route into a wrong one."""
    import chimera.providers.listing as listing_mod

    class _Listing:
        models: list[Any] = []
        sources: list[str] = ["catalog"]
        reason = ""

    monkeypatch.setattr(listing_mod, "available_models", lambda *a, **k: _Listing())
    client = _client(tmp_path)

    primeira = client.get("/api/models").json()
    segunda = client.get("/api/models").json()

    assert campo in segunda
    assert segunda[campo] == primeira[campo]
