"""Three edges the mutation gate found untested in the credibility modules (study 30, S30-37).

- `_fetch_latest`: two entries that parse to the same version keep the FIRST (GitHub's newest by date)
  rather than letting a later duplicate replace it — `>` against `>=` was invisible.
- `_cached_latest`: called with no argument it is the stable track; a default flipped to the
  prerelease list would offer candidates to every install that never opted in.
- `run_paired_experiment`: the treatment arm is handed the item, the same as the baseline arm.
"""

from __future__ import annotations

from typing import Any

import pytest

from chimera.api import version_api
from chimera.eval.paired import run_paired_experiment


@pytest.fixture(autouse=True)
def _clear_cache() -> Any:
    version_api._cache.clear()
    yield
    version_api._cache.clear()


def test_two_releases_with_the_same_version_keep_the_first_listed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    releases = [
        {"tag_name": "v1.2.0", "html_url": "https://example.test/first"},
        {"tag_name": "1.2.0", "html_url": "https://example.test/second"},
    ]
    monkeypatch.setattr(version_api, "_get_json", lambda url: releases)
    assert version_api._fetch_latest(include_prereleases=True) == ("1.2.0", "https://example.test/first")


def test_the_cached_check_defaults_to_the_stable_track(monkeypatch: pytest.MonkeyPatch) -> None:
    asked: list[bool] = []

    def fetch(*, include_prereleases: bool = False) -> tuple[str, str]:
        asked.append(include_prereleases)
        return "1.0.0", "https://example.test/r"

    monkeypatch.setattr(version_api, "_fetch_latest", fetch)
    version_api._cached_latest()
    assert asked == [False]


def test_both_arms_of_a_paired_run_are_handed_the_item() -> None:
    seen: list[tuple[str, int]] = []
    result = run_paired_experiment(
        [1, 2],
        restore=lambda item: None,
        baseline=lambda item: seen.append(("b", item)) or True,
        treatment=lambda item: seen.append(("t", item)) or item == 2,
    )
    assert seen == [("b", 1), ("t", 1), ("b", 2), ("t", 2)]
    assert result.n == 2
