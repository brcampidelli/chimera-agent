"""An app on a release candidate was told about nothing at all.

Two causes, stacked, and either alone was enough:

* ``_parse_version("0.48.0rc46")`` returned ``None`` — every segment had to be digits — so every
  comparison answered False. Not a newer candidate, not even the final ``0.48.0``.
* ``/releases/latest`` excludes prereleases by GitHub's own definition, so the newest thing the
  check could ever report was the last stable.

With forty-six candidates in this series that is the common case, not the edge — measured on a real
install running rc46: ``latest: "0.47.0", update_available: false``.

The property that must NOT change: a stable install is never offered a candidate. Someone on 0.47.0
did not opt into a prerelease track, and answering their update check with one would move them onto
a track they never chose.

Free: no network — every fetch is replaced.
"""

from __future__ import annotations

from typing import Any

import pytest

from chimera.api import version_api
from chimera.api.version_api import _is_newer, _is_prerelease, _parse_version


@pytest.fixture(autouse=True)
def _sem_cache() -> Any:
    """The module caches by track. A test that inherits another's entry is measuring that entry."""
    version_api._cache.clear()
    yield
    version_api._cache.clear()


# --- ordering -------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("mais_novo", "mais_velho"),
    [
        ("0.48.0rc46", "0.48.0rc45"),
        ("0.48.0rc46", "0.48.0rc9"),  # numeric, not lexicographic: "rc9" must not beat "rc46"
        ("0.48.0", "0.48.0rc46"),  # the final release outranks every candidate for it
        ("0.48.0rc1", "0.47.0"),
        ("1.0.0", "0.99.99"),
    ],
)
def test_the_newer_one_is_newer(mais_novo: str, mais_velho: str) -> None:
    assert _is_newer(mais_novo, mais_velho) is True
    assert _is_newer(mais_velho, mais_novo) is False


def test_the_same_version_is_not_an_update() -> None:
    assert _is_newer("0.48.0rc46", "0.48.0rc46") is False


@pytest.mark.parametrize("texto", ["0.0.0+source", "0.48.0.post1", "0.48.0dev3", "abc", "", "0.48"])
def test_anything_unrecognised_never_claims_an_update(texto: str) -> None:
    """Never a false positive is the rule this check has always had, and the parser getting more
    capable must not cost it. A source checkout in particular reports ``0.0.0+source``."""
    assert _parse_version(texto) is None
    assert _is_newer(texto, "0.48.0") is False
    assert _is_newer("0.48.0", texto) is False


def test_a_source_checkout_is_not_treated_as_a_prerelease() -> None:
    """It parses as nothing, and "nothing" must not be read as "opted into candidates"."""
    assert _is_prerelease("0.0.0+source") is False
    assert _is_prerelease("0.48.0") is False
    assert _is_prerelease("0.48.0rc46") is True


# --- which track is asked for ----------------------------------------------------------------------


def _fake_github(monkeypatch: Any, *, latest: Any, listagem: Any) -> list[str]:
    """Replace the network and record which URL was asked for."""
    pedidos: list[str] = []

    def _get(url: str) -> Any:
        pedidos.append(url)
        return listagem if "per_page" in url else latest

    monkeypatch.setattr(version_api, "_get_json", _get)
    return pedidos


def _release(tag: str, **kw: Any) -> dict[str, Any]:
    return {"tag_name": tag, "html_url": f"https://example/{tag}", **kw}


def test_a_stable_install_is_never_offered_a_candidate(monkeypatch: Any) -> None:
    """The safety property. `/releases/latest` excludes prereleases, and a stable install must keep
    asking exactly that endpoint — otherwise the parser's new understanding would push someone onto
    a track they never chose."""
    pedidos = _fake_github(
        monkeypatch,
        latest=_release("v0.47.0"),
        listagem=[_release("v0.48.0rc46"), _release("v0.47.0")],
    )
    monkeypatch.setattr(version_api, "_current_version", lambda: "0.47.0", raising=False)
    import chimera

    monkeypatch.setattr(chimera, "__version__", "0.47.0")

    resultado = version_api.check_version()

    assert all("per_page" not in u for u in pedidos), "a stable install asked for prereleases"
    assert resultado["latest"] == "0.47.0"
    assert resultado["update_available"] is False


def test_a_candidate_hears_about_a_newer_candidate(monkeypatch: Any) -> None:
    """The measured case: rc46 installed, rc47 published, and the app said nothing."""
    _fake_github(
        monkeypatch,
        latest=_release("v0.47.0"),
        listagem=[_release("v0.48.0rc47"), _release("v0.48.0rc46"), _release("v0.47.0")],
    )
    import chimera

    monkeypatch.setattr(chimera, "__version__", "0.48.0rc46")

    resultado = version_api.check_version()

    assert resultado["latest"] == "0.48.0rc47"
    assert resultado["update_available"] is True
    assert resultado["notes_url"] == "https://example/v0.48.0rc47"


def test_a_candidate_hears_about_the_final_release(monkeypatch: Any) -> None:
    """The half that matters most: the whole point of a candidate is to be replaced by 0.48.0."""
    _fake_github(
        monkeypatch,
        latest=_release("v0.48.0"),
        listagem=[_release("v0.48.0"), _release("v0.48.0rc46")],
    )
    import chimera

    monkeypatch.setattr(chimera, "__version__", "0.48.0rc46")

    resultado = version_api.check_version()

    assert resultado["latest"] == "0.48.0"
    assert resultado["update_available"] is True


def test_the_newest_is_by_version_not_by_list_order(monkeypatch: Any) -> None:
    """GitHub sorts the list by creation date. A patch cut AFTER a candidate would otherwise be
    announced as the newer of the two, which is the wrong answer in the one direction that moves
    someone backwards."""
    _fake_github(
        monkeypatch,
        latest=_release("v0.47.1"),
        listagem=[_release("v0.47.1"), _release("v0.48.0rc46")],  # newest-first by date
    )
    import chimera

    monkeypatch.setattr(chimera, "__version__", "0.48.0rc45")

    assert version_api.check_version()["latest"] == "0.48.0rc46"


def test_a_draft_is_not_a_release(monkeypatch: Any) -> None:
    """A draft is not published. Announcing one sends people to a page they cannot download."""
    _fake_github(
        monkeypatch,
        latest=_release("v0.47.0"),
        listagem=[_release("v0.49.0rc1", draft=True), _release("v0.48.0rc46")],
    )
    import chimera

    monkeypatch.setattr(chimera, "__version__", "0.48.0rc45")

    assert version_api.check_version()["latest"] == "0.48.0rc46"


def test_an_unreadable_tag_is_skipped_not_guessed(monkeypatch: Any) -> None:
    _fake_github(
        monkeypatch,
        latest=_release("v0.47.0"),
        listagem=[_release("nightly-2026-08-31"), _release("v0.48.0rc46")],
    )
    import chimera

    monkeypatch.setattr(chimera, "__version__", "0.48.0rc45")

    assert version_api.check_version()["latest"] == "0.48.0rc46"


# --- it still fails silently ------------------------------------------------------------------------


@pytest.mark.parametrize("resposta", [None, [], {}, "nao e json", [{"tag_name": ""}]])
def test_a_failed_fetch_is_never_an_update(monkeypatch: Any, resposta: Any) -> None:
    """The oldest rule here: offline, rate-limited or malformed all degrade to "no update", never
    to a claim and never to an exception."""
    _fake_github(monkeypatch, latest=resposta, listagem=resposta)
    import chimera

    monkeypatch.setattr(chimera, "__version__", "0.48.0rc46")

    resultado = version_api.check_version()

    assert resultado["latest"] is None
    assert resultado["update_available"] is False
    assert resultado["notes_url"] is None


def test_the_two_tracks_do_not_share_a_cache(monkeypatch: Any) -> None:
    """One slot for both questions would serve a stable install the candidate list."""
    _fake_github(
        monkeypatch,
        latest=_release("v0.47.0"),
        listagem=[_release("v0.48.0rc46")],
    )

    estavel = version_api._cached_latest(include_prereleases=False)
    candidata = version_api._cached_latest(include_prereleases=True)

    assert estavel[0] == "0.47.0"
    assert candidata[0] == "0.48.0rc46"
