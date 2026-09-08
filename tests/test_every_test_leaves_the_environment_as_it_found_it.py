"""The guard in ``conftest.py``, and the claims it takes on trust.

The guard fails any test that leaves ``os.environ`` different from how it found it, except for the
names in ``_CHANGES_NOBODY_OWNS``. An allowlist is only as honest as the claims beside its entries,
so each claim is checked here against the thing that makes it: pytest itself for one name, a fresh
interpreter importing litellm for the other. An entry nothing sets any more would excuse a real
leak forever — this is where it fails instead.

The same fresh interpreter proves the ``LITELLM_MODE`` line: litellm's import loads whatever
``.env`` python-dotenv finds walking up from where it runs, and the suite has to know that the
setting turns it off rather than assume so. The control — that WITHOUT the setting the file does
reach the environment — is what makes the first half evidence: a probe that cannot show the effect
cannot show its absence either.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.conftest import _CHANGES_NOBODY_OWNS

# Run with ``-c`` on purpose: python-dotenv treats a ``__main__`` without ``__file__`` as a REPL and
# searches from the working directory, which is the one place a test can put a ``.env`` of its own.
# Run from a file, it would search from litellm's package directory instead — and find the
# repository's ``.env``, or nothing.
_PROBE = (
    "import os, litellm; "
    "print(os.environ.get('TIKTOKEN_CACHE_DIR', '')); "
    "print(os.environ.get('CHIMERA_LEAK_PROBE', ''))"
)


def _import_litellm_beside_a_dotenv(tmp_path: Path, mode: str | None) -> tuple[str, str]:
    """Import litellm in a fresh interpreter whose cwd holds a ``.env``; return what it ended up with."""
    (tmp_path / ".env").write_text("CHIMERA_LEAK_PROBE=from-the-file\n", encoding="utf-8")
    scrubbed = ("TIKTOKEN_CACHE_DIR", "LITELLM_MODE", "CHIMERA_LEAK_PROBE")
    env = {k: v for k, v in os.environ.items() if k not in scrubbed}
    if mode is not None:
        env["LITELLM_MODE"] = mode
    out = subprocess.run(
        [sys.executable, "-c", _PROBE], capture_output=True, text=True, env=env, cwd=tmp_path,
        check=True,
    ).stdout
    tiktoken_cache_dir, probe = out.splitlines()[-2:]
    return tiktoken_cache_dir, probe


def test_a_variable_owned_through_monkeypatch_does_not_trip_the_guard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The ordering the guard depends on, asserted rather than assumed.

    The guard is defined before every other autouse fixture in ``conftest.py``, so it is set up
    first and torn down last — after ``monkeypatch`` has put things back. If that order ever
    inverts, this test starts failing while the whole suite fails with it, which is the loud way
    round: the alternative is a guard that quietly blames every test that sets a variable properly.
    """
    monkeypatch.setenv("CHIMERA_LEAK_PROBE", "1")
    assert os.environ["CHIMERA_LEAK_PROBE"] == "1"


def test_pytest_really_sets_the_name_the_allowlist_excuses(request: pytest.FixtureRequest) -> None:
    assert os.environ["PYTEST_CURRENT_TEST"].startswith(request.node.nodeid)


def test_litellm_really_sets_the_other_name_and_leaves_the_dotenv_alone(tmp_path: Path) -> None:
    tiktoken_cache_dir, probe = _import_litellm_beside_a_dotenv(tmp_path, "PRODUCTION")
    assert tiktoken_cache_dir, "litellm no longer sets TIKTOKEN_CACHE_DIR at import — drop the entry"
    assert probe == "", "LITELLM_MODE=PRODUCTION no longer stops litellm from loading the .env"


def test_without_the_setting_the_dotenv_does_reach_the_environment(tmp_path: Path) -> None:
    """The control: the mechanism the setting turns off is real, so the test above measures it."""
    _tiktoken_cache_dir, probe = _import_litellm_beside_a_dotenv(tmp_path, None)
    assert probe == "from-the-file"


def test_the_allowlist_names_nothing_this_file_does_not_check() -> None:
    assert {"PYTEST_CURRENT_TEST", "TIKTOKEN_CACHE_DIR"} == _CHANGES_NOBODY_OWNS
