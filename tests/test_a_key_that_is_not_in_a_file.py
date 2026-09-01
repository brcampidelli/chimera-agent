"""Twelve credentials in plain text, readable by anything running as this user — including the agent.

`grep -rn "keyring|keychain|libsecret|DPAPI" chimera/` returned nothing. Every provider key lived in
an environment variable and, for most installs, in a `.env` beside the project: readable by any
process with the user's rights, and by the agent itself the moment somebody asks it to `cat` the
wrong file. This project has already paid for that twice, with an OpenRouter key and a PassaPro
token found in cleartext.

macOS, Windows and most Linux desktops ship a vault for exactly this. `keyring` is the one library
that speaks to all three, and it is an OPTIONAL extra — a container has no keychain, a server has no
session bus, and a tool that refused to start without one would be worse than the file it replaces.

The tests split three ways, and the middle one carries the most weight: what happens with no vault
at all has to be exactly what happens today.
"""

from __future__ import annotations

import pytest

from chimera import config_vault

#: The stand-in for a credential, deliberately shaped like nothing. The first draft used
#: `sk-or-v1-...`, and the repository's own secret scanner stopped the pull request on it — correctly,
#: because a scanner that could tell a test's fake key from a real one cannot exist. The fix is a
#: fixture with no key shape, never an allowlist entry: that would carve a hole in this exact file.
VALOR = "nao-e-uma-chave-de-verdade"


class _CofreFalso:
    """A vault in memory, with the surface `keyring` exposes."""

    def __init__(self, *, quebrado: bool = False) -> None:
        self._dados: dict[tuple[str, str], str] = {}
        self._quebrado = quebrado

    def set_password(self, service: str, name: str, value: str) -> None:
        if self._quebrado:
            raise RuntimeError("keychain locked")
        self._dados[(service, name)] = value

    def get_password(self, service: str, name: str) -> str | None:
        if self._quebrado:
            raise RuntimeError("keychain locked")
        return self._dados.get((service, name))

    def delete_password(self, service: str, name: str) -> None:
        if self._quebrado or (service, name) not in self._dados:
            raise RuntimeError("not found")
        del self._dados[(service, name)]


@pytest.fixture
def cofre(monkeypatch: pytest.MonkeyPatch) -> _CofreFalso:
    falso = _CofreFalso()
    monkeypatch.setattr(config_vault, "_keyring", lambda: falso)
    return falso


# ------------------------------------------------------------------ with a vault


def test_a_key_goes_in_and_comes_back(cofre: _CofreFalso) -> None:
    ambiente: dict[str, str] = {}

    assert config_vault.store("OPENROUTER_API_KEY", VALOR) is True
    assert config_vault.load_into_environment(ambiente) == ["OPENROUTER_API_KEY"]
    assert ambiente["OPENROUTER_API_KEY"] == VALOR


def test_the_environment_always_wins(cofre: _CofreFalso) -> None:
    """The property that makes this safe to add to an install that works.

    Somebody running `OPENROUTER_API_KEY=… chimera solve` is making a deliberate, visible choice for
    one command. A vault that overrode it would be a setting that cannot be overridden from the
    shell — the one place people expect to be able to.
    """
    config_vault.store("OPENROUTER_API_KEY", "do-cofre")
    ambiente = {"OPENROUTER_API_KEY": "da-linha-de-comando"}

    assert config_vault.load_into_environment(ambiente) == []
    assert ambiente["OPENROUTER_API_KEY"] == "da-linha-de-comando"


def test_only_credentials_may_be_stored(cofre: _CofreFalso) -> None:
    """An allowlist, not "any variable". Letting the vault carry `CHIMERA_DEFAULT_MODEL` would turn
    a security boundary into a second, invisible config file that nobody looks in when a setting is
    wrong."""
    assert config_vault.store("CHIMERA_DEFAULT_MODEL", "openrouter/x") is False
    assert config_vault.load_into_environment({}) == []


def test_the_listing_never_prints_a_value(cofre: _CofreFalso) -> None:
    """A listing that showed secrets would put them in scrollback, in a screenshot, and in whatever
    recorded the session — undoing the entire point of the vault."""
    config_vault.store("OPENROUTER_API_KEY", VALOR)

    listado = config_vault.stored()

    assert listado == ["OPENROUTER_API_KEY"]
    assert all(VALOR not in entrada for entrada in listado)


def test_forgetting_a_key_removes_it(cofre: _CofreFalso) -> None:
    config_vault.store("GITHUB_TOKEN", VALOR)

    assert config_vault.forget("GITHUB_TOKEN") is True
    assert config_vault.stored() == []


def test_forgetting_what_is_not_there_says_so(cofre: _CofreFalso) -> None:
    assert config_vault.forget("GITHUB_TOKEN") is False


# ------------------------------------------------------------------ with no vault


def test_with_no_vault_nothing_changes(monkeypatch: pytest.MonkeyPatch) -> None:
    """The case that decides whether this may be added at all: a container, a server, a machine
    without `keyring` installed. Every one of them must behave exactly as it does today."""
    monkeypatch.setattr(config_vault, "_keyring", lambda: None)
    ambiente = {"OPENROUTER_API_KEY": "do-arquivo"}

    assert config_vault.available() is False
    assert config_vault.load_into_environment(ambiente) == []
    assert config_vault.store("OPENROUTER_API_KEY", "x") is False
    assert config_vault.stored() == []
    assert ambiente == {"OPENROUTER_API_KEY": "do-arquivo"}


def test_a_locked_vault_fills_nothing_and_breaks_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    """A keychain that is present and refuses — locked, or the user cancelled the prompt. The run
    continues on whatever the environment has, which is the same fallback as having no vault."""
    monkeypatch.setattr(config_vault, "_keyring", lambda: _CofreFalso(quebrado=True))
    ambiente = {"OPENROUTER_API_KEY": "do-arquivo"}

    assert config_vault.load_into_environment(ambiente) == []
    assert config_vault.store("OPENROUTER_API_KEY", "x") is False
    assert ambiente["OPENROUTER_API_KEY"] == "do-arquivo"


def test_a_dead_backend_is_no_vault(monkeypatch: pytest.MonkeyPatch) -> None:
    """`keyring` installs everywhere and finds a backend nowhere in particular.

    On a headless Linux box it resolves to `backends.fail.Keyring`, which is present, importable,
    and raises on every call. Counting that as a vault would make `chimera secrets set` report
    success and store nothing.

    Written first as `_keyring() is None or available() is True`, which is a tautology: it holds
    whichever branch is taken, so removing the check under it changed nothing. Rewritten to import
    the real `keyring.backends.fail`, it then passed on a machine WITHOUT `keyring` — by raising
    `ModuleNotFoundError` in the test itself. Both versions went green for reasons unrelated to the
    code under test. The module tree below is built by hand so the test says the same thing whether
    or not the optional extra is installed.
    """
    import sys
    import types

    class _Fail:
        """`keyring.backends.fail.Keyring`: present, importable, raises on everything."""

    fail = types.ModuleType("keyring.backends.fail")
    fail.Keyring = _Fail  # type: ignore[attr-defined]
    backends = types.ModuleType("keyring.backends")
    backends.fail = fail  # type: ignore[attr-defined]
    biblioteca = types.ModuleType("keyring")
    biblioteca.backends = backends  # type: ignore[attr-defined]
    biblioteca.get_keyring = lambda: _Fail()  # type: ignore[attr-defined]
    for nome, modulo in [
        ("keyring", biblioteca),
        ("keyring.backends", backends),
        ("keyring.backends.fail", fail),
    ]:
        monkeypatch.setitem(sys.modules, nome, modulo)

    assert config_vault._keyring() is None
    assert config_vault.available() is False


def test_it_only_reads_the_names_it_is_allowed_to_store(cofre: _CofreFalso) -> None:
    """The loader's half of the allowlist. `store` refuses a non-credential; the loader must not
    read one either, or a vault written by hand becomes a second, invisible config file."""
    cofre.set_password(config_vault.SERVICE, "CHIMERA_DEFAULT_MODEL", "openrouter/x")
    ambiente: dict[str, str] = {}

    assert config_vault.load_into_environment(ambiente) == []
    assert ambiente == {}
