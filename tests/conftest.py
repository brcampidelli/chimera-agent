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
import sys
from collections import Counter
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from chimera.config import Settings, get_settings
from chimera.providers.discovery import FIRST_CLASS, NOT_A_MODEL_PROVIDER, provider_from_env_var
from tests import shards
from tests.network_guard import (
    ALLOWLIST_FILE,
    OPT_OUT_MARKERS,
    is_allowlisted,
    load_allowlist,
    refusing_outside_connections,
)


def _test_file(item: pytest.Item, root: Path) -> str:
    """The file an item came from, relative to the rootdir and with forward slashes on every OS.

    The same file has to produce the same key on the Windows runner and on Linux, or the two would
    disagree about which shard owns it the day the split is compared across them.
    """
    try:
        return item.path.relative_to(root).as_posix()
    except ValueError:
        return item.path.as_posix()


@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Keep only this shard's files when ``CI_TEST_SHARD`` is set (`tests/shards.py` says why).

    ``trylast`` so the count each file is weighed by is what will actually run here, after the
    marker deselection (``-m 'not integration'``) has had its turn.
    """
    spec = os.environ.get(shards.ENV, "").strip()
    if not spec:
        return
    try:
        index, total = shards.parse(spec)
    except ValueError as exc:
        raise pytest.UsageError(str(exc)) from None
    files = [_test_file(item, config.rootpath) for item in items]
    owner = shards.assign(Counter(files), total)
    keep = [item for item, path in zip(items, files, strict=True) if owner[path] == index]
    drop = [item for item, path in zip(items, files, strict=True) if owner[path] != index]
    if drop:
        config.hook.pytest_deselected(items=drop)
    items[:] = keep
    reporter = config.pluginmanager.get_plugin("terminalreporter")
    if reporter is not None:
        mine = sorted({path for path in files if owner[path] == index})
        reporter.write_line(
            f"{shards.ENV}={index}/{total}: {len(mine)} of {len(owner)} files, "
            f"{len(keep)} of {len(files)} tests"
        )


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

# The same leak, a second library, and this one has no switch of its own: importing ``markitdown``
# (the ``documents`` extra, reached through ``chimera.scrape.clean``) imports ``magika``, whose
# ``__init__`` calls ``dotenv.load_dotenv(dotenv.find_dotenv())`` outright — no mode to set, no
# ``env_file`` to override. python-dotenv's own kill switch covers every library at once, so it is
# set here rather than chasing each one that decides to load a ``.env``. Without it the suite
# carried the developer's ``OPENROUTER_API_KEY`` and ``CHIMERA_CHAT_MEMORY`` from the first test
# that touched the scrape path. Proven, with its control, by the same file as the line above.
os.environ["PYTHON_DOTENV_DISABLED"] = "1"


# Changes to ``os.environ`` that no test owns, so the guard below must not blame a test for them.
# Every entry is proven live by tests/test_every_test_leaves_the_environment_as_it_found_it.py: an
# entry nothing sets any more is a hole in the guard, not a harmless leftover.
_CHANGES_NOBODY_OWNS: frozenset[str] = frozenset(
    {
        # pytest's own bookkeeping: "<nodeid> (setup)" when the snapshot is taken, "<nodeid>
        # (teardown)" when it is compared.
        "PYTEST_CURRENT_TEST",
        # litellm assigns it on first tokenizer use (litellm_core_utils/default_encoding.py, imported
        # lazily since 1.105), once per process, inside whichever test happens to count tokens first.
        "TIKTOKEN_CACHE_DIR",
        # sklearn's ``__init__`` calls ``os.environ.setdefault`` for both (sklearn/__init__.py:56,60)
        # to let two OpenMP runtimes coexist. Reached through ``chimera.tools.media``, which imports
        # torch/diffusers lazily for image generation — so the first test that touches that path
        # writes them, and no test owns them.
        "KMP_DUPLICATE_LIB_OK",
        "KMP_INIT_AT_FORK",
        # torch's inductor cache, assigned at import (torch/_dynamo/package.py) the same way and for
        # the same reason: a per-user cache directory, decided once per process.
        "TORCHINDUCTOR_CACHE_DIR",
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
def _the_developers_claude_settings_are_not_read(
    monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory
) -> None:
    """Point ``CLAUDE_CONFIG_DIR`` at an empty folder.

    Claude Code compatibility is on by default (`chimera/governance/claude_hooks.py`), so a stock
    ``Settings`` reads ``~/.claude/settings.json``; a maintainer whose own settings carry hooks would
    otherwise see "no hooks, same registry" tests fail on their machine and pass in CI.
    """
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path_factory.mktemp("claude-config")))


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
def _no_backend_log_outlives_its_test() -> Iterator[None]:
    """Detach the on-disk warning log a test's `serve` or `app` attached, and forget its path.

    `chimera serve` attaches it too now, and a dozen tests drive `serve`. The handler sits on the
    process-wide ``chimera`` logger and the path in a module global, so without this every later
    test would write its warnings into an earlier test's temp folder, and a failure sentence that
    names "where the details are" would name that folder — passing or failing on collection order.
    """
    import logging
    from logging.handlers import RotatingFileHandler

    from chimera import file_log

    yield
    root = logging.getLogger("chimera")
    for handler in list(root.handlers):
        if isinstance(handler, RotatingFileHandler):
            root.removeHandler(handler)
            handler.close()
    file_log._FILE_LOG = None


@pytest.fixture(autouse=True)
def _no_dotenv(
    monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory
) -> Iterator[None]:
    config = dict(Settings.model_config)
    config["env_file"] = None
    monkeypatch.setattr(Settings, "model_config", config)
    # Claude Code's owner folder (`~/.claude`) is read for subagents (`claude_agent_format.claude_home`).
    # A developer who uses Claude Code has agents there, and a discovery test that lists "every agent"
    # would see them. An empty folder per test keeps the suite the same on every machine.
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path_factory.mktemp("claude-config")))
    # A gateway can export a configured single provider key into ``os.environ`` for LiteLLM.
    # Own the names before removing them so that export is undone after each test, including when
    # the test process started with a key in its shell. The first-class names also cover keys
    # loaded from a dotenv file, which were not necessarily present in the incoming environment.
    provider_keys = set(_model_provider_keys()) | FIRST_CLASS
    # Own every provider credential name, including names introduced during this test, so a
    # gateway export that was absent at setup is restored to absent at teardown.
    provider_keys.update(
        name for name in os.environ
        if name not in NOT_A_MODEL_PROVIDER and provider_from_env_var(name) is not None
    )
    for name in provider_keys:
        monkeypatch.setenv(name, "")
        monkeypatch.delenv(name)
    # `LLMGateway.__init__` exports OLLAMA_API_BASE into os.environ for real when it is unset, and
    # 325 tests build a gateway. Own the name once, here: setenv records whatever was there (or
    # that nothing was), delenv keeps it out of the test, and the teardown puts that back.
    monkeypatch.setenv("OLLAMA_API_BASE", "")
    monkeypatch.delenv("OLLAMA_API_BASE")
    # Same export, second runtime: LiteLLM's `lm_studio/` provider has no default base URL, so the
    # gateway exports `LM_STUDIO_API_BASE` the way it exports Ollama's (see `Settings.lm_studio_base_url`).
    monkeypatch.setenv("LM_STUDIO_API_BASE", "")
    monkeypatch.delenv("LM_STUDIO_API_BASE")
    # `chimera app` writes CHIMERA_GOVERNANCE=observe into the process environment when nobody
    # chose a mode (`_kernel_observes_unless_told_otherwise`), and the app-factory tests build that
    # command for real. Owned the same way, so the desktop's default is a fact those tests can read
    # and not a change they are blamed for — and so a developer's own shell value never reaches a test.
    monkeypatch.setenv("CHIMERA_GOVERNANCE", "")
    monkeypatch.delenv("CHIMERA_GOVERNANCE")
    # The desktop app exports the owner's posture into the process environment, so a developer who
    # has run it carries `CHIMERA_REACH=workspace_shell` in their shell — and pydantic-settings reads
    # it into every `Settings(...)` a test builds, including the ones that assert the STOCK default.
    # `test_read_config_reports_autonomy_as_configured` failed exactly there: it asked for
    # `Settings(CHIMERA_HOME=...)` and got the developer's reach back, so the assertion read
    # `workspace_shell` where it expected `""`. Owned the same way as the governance flag above, so
    # the shipped default is a fact a test can read rather than a property of whose machine ran it.
    monkeypatch.setenv("CHIMERA_REACH", "")
    monkeypatch.delenv("CHIMERA_REACH")
    # Same reason, a newer switch: `SpendBudget()` reads CHIMERA_STRICT_SPEND_CAP when it is built,
    # and `patch_config` exports what it saves into os.environ. A shell that inherited `true` made
    # every phase-0 test that builds `SpendBudget(max_usd)` without `strict=` red on that machine
    # only (the $1.20 reservation tests, the agent loop's four calls). Owned, so off is the default
    # every test sees, and a test that wants strict says so.
    monkeypatch.setenv("CHIMERA_STRICT_SPEND_CAP", "")
    monkeypatch.delenv("CHIMERA_STRICT_SPEND_CAP")
    # The owner's deployment policy file. A VPS-shaped shell exporting it would hand every test a
    # real install's denies; owned so the stock answer (`<home>/policy.json`, absent) is the default.
    monkeypatch.setenv("CHIMERA_POLICY_FILE", "")
    monkeypatch.delenv("CHIMERA_POLICY_FILE")
    # The desktop bridge's discovery file lives in the user's REAL home (`~/.chimera`), outside
    # `CHIMERA_HOME` on purpose (`chimera/api/desktop_bridge.py`). Every test gets a private
    # directory for it, so no test can write — or delete — the file of an app the developer has
    # open. The two switches are owned for the same reason `CHIMERA_REACH` is: off, as shipped.
    monkeypatch.setenv("CHIMERA_BRIDGE_DIR", str(tmp_path_factory.mktemp("bridge")))
    # The background service's record (S1) lives in the same real `~/.chimera`, and since S2
    # `chimera code` prefers it: a developer with `chimera service start` running would otherwise
    # have every terminal test drive their own service. Private here too, and the channel pinned.
    monkeypatch.setenv("CHIMERA_SERVICE_DIR", str(tmp_path_factory.mktemp("service")))
    monkeypatch.setenv("CHIMERA_SERVICE_CHANNEL", "dev")
    for name in ("CHIMERA_DESKTOP_BRIDGE", "CHIMERA_DESKTOP_BRIDGE_FULL", "CHIMERA_BRIDGE_FULL_CONTROL"):
        monkeypatch.setenv(name, "")
        monkeypatch.delenv(name)
    # The data folder too. A test that runs `chimera app` without naming one served the developer's
    # real home, and since the app claims its folder (`chimera/core/instance.py`, R12 of 2026-09-30)
    # it would have taken the developer's own app's lock, or failed because that app held it. A test
    # that needs a particular home still sets its own; this is only the default under it.
    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path_factory.mktemp("home")))
    # Claude Code's own folder (`~/.claude`) is the developer's real one too: the instruction files in it
    # reach every composed prompt when `CHIMERA_CLAUDE_COMPAT` is on. Private here, so no test reads them.
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path_factory.mktemp("claude-config")))
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _prices_put_back() -> Iterator[None]:
    """The price table as it was, after every test.

    `set_price` inserts into a process-wide table and nothing undid it, so a price a test pinned for
    its own arithmetic reached every later test: one pinned deepseek-chat at US$ 100 per million and
    a receipt test billed 12k tokens at US$ 1.20 (found 2026-09-30, by bisecting the suite). The
    shared `.chimera` folder's price cache had hidden it until each test got its own folder.

    The flag that says the shipped catalogue was folded in goes back with it. The catalogue is folded
    in once, on the first lookup; restoring the table without the flag would drop those prices and
    leave the flag saying they are there, so every later test would price the catalogue as unknown.
    """
    from chimera.fusion import receipts

    saved, registered = list(receipts._PRICES), receipts._catalog_registered
    yield
    receipts._PRICES[:] = saved
    receipts._catalog_registered = registered




@pytest.fixture(autouse=True)
def _built_ins_declared_as_before_the_default_changed(request: pytest.FixtureRequest) -> Iterator[None]:
    """Pin `CHIMERA_DEFER_TOOLS` to the value it had before 2026-11-10: off, so built-ins are declared.

    Deferral is on by default now: the built-ins outside files, search and shell sit behind
    `tool_list` / `tool_call` instead of in the declared list. Most tests here are about something else
    (a gate, a screen, a governance rule, a CLI contract), and they assert on the declared names they
    were written against, so they run with the old shape. Tests whose subject IS the default, or the
    deferral itself, set the switch in the environment, or remove the pin with
    the `product_default` marker: `tests/test_the_product_defers_the_builtins_by_default.py` and the
    deferral tests named there carry it.

    The pin is applied where a `Settings` is built and no value is set in the environment or the call. It
    is not written to `os.environ`: an environment write here is seen by the hygiene fixture above as a
    leak from the test body, and it is not one.

    Limits of the pin, so nobody trusts it further than it goes: it only sees a `Settings` built with the
    `CHIMERA_DEFER_TOOLS` key absent from both the kwargs and `os.environ`. A value read from a dotenv file
    (`.env` in the working directory) is not in either, so the pin overrides it with `False`; a
    `Settings.model_validate(...)` / `model_construct` path never calls `__init__` with these kwargs, so it
    gets the product default (on). A test that depends on either must set the value itself.
    """
    from chimera.config import Settings

    original = Settings.__init__

    def declared_by_default(self: Settings, *args: Any, **kwargs: Any) -> None:
        if "CHIMERA_DEFER_TOOLS" not in kwargs and "CHIMERA_DEFER_TOOLS" not in os.environ:
            kwargs["CHIMERA_DEFER_TOOLS"] = False
        original(self, *args, **kwargs)

    if request.node.get_closest_marker("product_default") is not None:
        yield  # the subject is the product's default: no pin
        return
    original_init = Settings.__init__
    Settings.__init__ = declared_by_default  # type: ignore[method-assign]
    try:
        yield
    finally:
        Settings.__init__ = original_init  # type: ignore[method-assign]


_NETWORK_ALLOWLIST = load_allowlist(Path(__file__).with_name(ALLOWLIST_FILE))


@pytest.fixture(autouse=True)
def _no_test_reaches_a_real_server(request: pytest.FixtureRequest) -> Iterator[None]:
    """Fail a test that opens a socket to anything but this machine (`tests/network_guard.py` says why).

    Opt out with ``@pytest.mark.network``; ``integration`` tests are already allowed, since reaching the
    provider is what they are for.
    """
    if sys.platform == "win32" or is_allowlisted(request.node.nodeid, _NETWORK_ALLOWLIST) or any(
        request.node.get_closest_marker(name) is not None for name in OPT_OUT_MARKERS
    ):
        # Linux is where this is enforced. On Windows the asyncio Proactor loop connects without
        # going through `socket.connect`, so the guard sees only part of the traffic there; and on the
        # Windows CI runner the litellm tests in test_an_ollama_call_asks_for_its_context_window.py and
        # test_an_answer_filed_as_reasoning_is_flagged_not_promoted.py reach 57.150.192.193:443 while
        # the same tests open nothing on Linux. Until that is understood, a partial guard on one OS
        # would only fail tests for a reason the other OS cannot reproduce.
        yield
        return
    violations: list[str] = []
    with refusing_outside_connections(violations):
        yield
    if violations:
        pytest.fail("test connected outside this machine: " + ", ".join(violations), pytrace=False)
