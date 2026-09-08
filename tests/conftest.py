"""Shared test fixtures.

Tests must be hermetic: a developer's real ``.env`` (with live provider keys) must never leak into
the suite, or "without key" tests would see a key and fail. This autouse fixture disables ``.env``
loading for every test, so only the OS environment (which tests drive via ``monkeypatch``)
determines configuration.

It also clears provider keys out of that environment, which the ``.env`` half alone does not do.
That gap was survivable while Chimera recognised exactly five credentials — each test that needed
"no key" deleted those five by hand, and anything else in the environment was ignored by definition.
It stopped being survivable when the credential gate learned to discover ANY ``<PROVIDER>_API_KEY``:
a maintainer with a Groq key exported in their shell would see tests fail that pass in CI, which is
the worst kind of red because it accuses the wrong change. Search and speech credentials are left
alone — they are not providers of models and no gate consults them.

Both halves above were written about pydantic-settings, and that is the second path they miss:
importing litellm runs python-dotenv itself, which walks up from wherever the import happens and
finds the same developer ``.env``. Turning it off is one assignment, made below at import time.

The third leak runs the other way — a test writing the environment for everyone after it, which
``monkeypatch`` cannot undo because it never went through ``monkeypatch``. The guard below fails
the test that does it, which is how the two halves stay true for the test that runs next.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest

from chimera.config import Settings, get_settings
from chimera.providers.discovery import FIRST_CLASS, NOT_A_MODEL_PROVIDER, provider_from_env_var


def _model_provider_keys() -> list[str]:
    return [
        name
        for name in os.environ
        if name not in NOT_A_MODEL_PROVIDER
        and (name in FIRST_CLASS or provider_from_env_var(name) is not None)
    ]


# litellm runs python-dotenv's ``load_dotenv()`` when it is imported (``litellm/__init__.py``) unless
# this says otherwise, and ``find_dotenv`` walks up from wherever the import happens — which, in a
# checkout, ends at the developer's own ``.env`` beside ``pyproject.toml``. The ``env_file = None``
# below is pydantic-settings' switch and never reaches that path: a full run on a developer machine
# carried the real ``OPENROUTER_API_KEY`` and ``CHIMERA_CHAT_MEMORY=false`` from collection onwards.
# Set here because this file is imported before any test module. Proven, with its control, by
# tests/test_every_test_leaves_the_environment_as_it_found_it.py.
os.environ["LITELLM_MODE"] = "PRODUCTION"


# Changes to ``os.environ`` that no test owns, so the guard below must not blame a test for them.
# Every entry is proven live by tests/test_every_test_leaves_the_environment_as_it_found_it.py: an
# entry nothing sets any more is a hole in the guard, not a harmless leftover.
_CHANGES_NOBODY_OWNS: frozenset[str] = frozenset(
    {
        # pytest's own bookkeeping: "<nodeid> (setup)" when the snapshot is taken, "<nodeid>
        # (teardown)" when it is compared.
        "PYTEST_CURRENT_TEST",
        # litellm assigns it at import (litellm_core_utils/default_encoding.py), once per process,
        # inside whichever test happens to import litellm first.
        "TIKTOKEN_CACHE_DIR",
    }
)


@pytest.fixture(autouse=True)
def _every_test_leaves_the_environment_as_it_found_it(
    request: pytest.FixtureRequest,
) -> Iterator[None]:
    """Fail the test that leaves ``os.environ`` different from how it found it, naming the keys.

    ``patch_config``, ``chimera init`` and the gateway's key export write the process environment
    for real, and ``monkeypatch`` only undoes what went through it. A value left behind makes the
    suite order-dependent: #386 found seven tests failing because an earlier one had left
    ``CHIMERA_SANDBOX=docker`` for the verifier to find. Here the test that leaked is the one that
    fails, and the environment is put back so it fails alone.

    Defined first in this file on purpose: autouse fixtures are set up in definition order and torn
    down in reverse, so the snapshot is taken before ``_no_dotenv`` deletes anything and compared
    after ``monkeypatch`` has put everything back. Session- and module-scoped fixtures sit outside
    that window on both sides, so one that sets a variable on purpose needs no allowlist entry.
    The way to leave a variable set is to own it first — ``monkeypatch.setenv(NAME, current)``
    before the call that writes it. ``delenv`` on a name that is absent records nothing and
    restores nothing; to own an absent name, ``setenv`` it and then ``delenv`` it.

    Failing after the test body has run, this is reported as an error at teardown rather than a
    failed assertion — the run goes red either way, and the message names the test and the keys.
    """
    before = dict(os.environ)
    yield
    after = dict(os.environ)
    added = sorted(k for k in after if k not in before and k not in _CHANGES_NOBODY_OWNS)
    removed = sorted(k for k in before if k not in after and k not in _CHANGES_NOBODY_OWNS)
    changed = sorted(
        k for k in after if k in before and after[k] != before[k] and k not in _CHANGES_NOBODY_OWNS
    )
    if not (added or removed or changed):
        return
    for name in added:
        del os.environ[name]
    for name in removed + changed:
        os.environ[name] = before[name]
    pytest.fail(
        f"{request.node.nodeid} left os.environ different from how it found it: "
        f"added {added}, removed {removed}, changed {changed}. Own the name before the call that "
        "writes it — monkeypatch.setenv(NAME, current); delenv on an absent name restores nothing.",
        pytrace=False,
    )


@pytest.fixture(autouse=True)
def _nobody_has_declared_the_terminal_dead() -> Iterator[None]:
    """Clear the process-wide "no human here" flag between tests.

    ``build_api_app`` sets it, and it is deliberately process-wide — the fact it records is a fact
    about the process. That makes it leak across tests in one session, and the leak is silent and
    directional: every test after the first API test would see the headless answer. The TTY
    auto-detection tests assert the *interactive* one, so without this they pass or fail on
    collection order, which is the kind of failure that shows up in CI and nowhere else.
    """
    import chimera.sandbox.confirm as confirm_mod

    confirm_mod._no_human_surface = None
    yield
    confirm_mod._no_human_surface = None


@pytest.fixture(autouse=True)
def _no_dotenv(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    config = dict(Settings.model_config)
    config["env_file"] = None
    monkeypatch.setattr(Settings, "model_config", config)
    for name in _model_provider_keys():
        monkeypatch.delenv(name, raising=False)
    # `LLMGateway.__init__` exports OLLAMA_API_BASE into os.environ for real when it is unset, and
    # 325 tests build a gateway. Own the name once, here: setenv records whatever was there (or
    # that nothing was), delenv keeps it out of the test, and the teardown puts that back.
    monkeypatch.setenv("OLLAMA_API_BASE", "")
    monkeypatch.delenv("OLLAMA_API_BASE")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
