"""The Settings screen can keep a key in the OS vault instead of in `.env` (study 29, P7.7).

The vault existed — `chimera secrets set`, and the startup read in `get_settings` — but the screen
where most desktop owners type their keys wrote every one of them into `.env` in plain text. Behind
`CHIMERA_KEY_VAULT` (off by default) a save now goes to the vault and leaves a comment in the file.

What is pinned here, all against an in-memory vault (no test touches a real keychain or a real key):

* off is the old behaviour, and asks the vault nothing — not on a save, not on a read; the one
  change every save now makes, switch on or off, is to leave a single entry for the key it writes
  (dotenv takes the LAST, so a second one kept the old value in force), keeping its `export`;
* on, the key is in the vault, read back, the file holds a COMMENT (never a placeholder value, which
  every reader of the file would take for the key), and the next launch reads the key back;
* on with no vault falls back to the file and says so; a vault that refuses fails the whole save and
  undoes what it had stored; a vault that says yes and keeps nothing counts as a refusal;
* the move between file and vault is reversible, never deletes an original before the copy is read
  back, and moves every cleartext copy (duplicates, `export`);
* the frozen desktop build reads the vault at startup only when the file marks a key as moved;
* the bridge can neither flip the switch nor reach the move.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import pytest

from chimera import config_vault
from chimera.api import key_vault
from chimera.api.bridge_routes import OWNER_ONLY_SETTINGS, ROUTES
from chimera.api.config_api import (
    is_editable,
    patch_config,
    pool_add,
    read_config,
    vault_move,
)
from chimera.config import Settings, get_settings

#: Shaped like nothing a scanner would call a key, for the reason `test_a_key_that_is_not_in_a_file`
#: gives: a fixture that looked like a real key stopped a pull request once.
CHAVE = "valor-de-teste-que-nao-e-chave-1234"
OUTRA = "outro-valor-de-teste-nao-chave-5678"


class _Cofre:
    """An in-memory vault with `keyring`'s surface, which counts every read."""

    def __init__(self, *, recusa: bool = False, esquece: bool = False) -> None:
        self.dados: dict[tuple[str, str], str] = {}
        self.leituras = 0
        self.escritas = 0
        self._recusa = recusa
        self._esquece = esquece  # says yes to a write and keeps nothing
        self.recusa_apagar = False  # a delete the keychain refuses (locked, a denied ACL prompt)

    def set_password(self, service: str, name: str, value: str) -> None:
        self.escritas += 1
        if self._recusa:
            raise RuntimeError("keychain locked")
        if not self._esquece:
            self.dados[(service, name)] = value

    def get_password(self, service: str, name: str) -> str | None:
        self.leituras += 1
        return self.dados.get((service, name))

    def delete_password(self, service: str, name: str) -> None:
        if self.recusa_apagar:
            raise RuntimeError("keychain locked")
        if (service, name) not in self.dados:
            raise RuntimeError("not found")
        del self.dados[(service, name)]

    def tem(self, name: str) -> str | None:
        return self.dados.get((config_vault.SERVICE, name))


@pytest.fixture(autouse=True)
def _isolado(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    """A temp cwd, every storable credential owned by monkeypatch (the startup read and
    `patch_config` both write `os.environ` for real), and NO real vault reachable: `_keyring`
    answers None unless a test hands it a fake."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(config_vault, "_keyring", lambda: None)
    for name in (*config_vault.STORABLE, "CHIMERA_KEY_VAULT"):
        monkeypatch.setenv(name, "")
    monkeypatch.delattr(sys, "frozen", raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _cofre(monkeypatch: pytest.MonkeyPatch, **kwargs: bool) -> _Cofre:
    falso = _Cofre(**kwargs)
    monkeypatch.setattr(config_vault, "_keyring", lambda: falso)
    return falso


def _env(tmp_path: Path) -> str:
    path = tmp_path / ".env"
    return path.read_text(encoding="utf-8") if path.exists() else ""


# ------------------------------------------------------------------ off: nothing changes


def test_the_switch_ships_off_and_is_a_setting_the_screen_can_write() -> None:
    assert Settings().key_vault is False
    assert is_editable("CHIMERA_KEY_VAULT")


def test_off_a_key_goes_to_the_file_and_the_vault_is_never_asked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cofre = _cofre(monkeypatch)

    result = patch_config({"OPENROUTER_API_KEY": CHAVE}, env_path=tmp_path / ".env")

    assert f"OPENROUTER_API_KEY={CHAVE}" in _env(tmp_path)
    assert result == {"updated": ["OPENROUTER_API_KEY"]}  # the answer it gave before the vault
    assert cofre.escritas == 0 and cofre.dados == {}


def test_off_and_unmarked_opening_settings_asks_the_vault_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An owner who never opted in gets no keychain access from reading the screen either — on
    Linux that read can open an unlock prompt nobody asked for."""
    cofre = _cofre(monkeypatch)

    snapshot = read_config(Settings(), env_path=tmp_path / ".env")

    assert snapshot["vault"] == {"enabled": False, "available": True, "keys": []}
    assert cofre.leituras == 0


# ------------------------------------------------------------------ on, with a vault


def test_on_the_key_is_in_the_vault_and_the_file_holds_only_a_comment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cofre = _cofre(monkeypatch)
    (tmp_path / ".env").write_text(f"OPENROUTER_API_KEY={OUTRA}\nCHIMERA_CACHE=true\n", "utf-8")

    result = patch_config(
        {"CHIMERA_KEY_VAULT": "true", "OPENROUTER_API_KEY": CHAVE}, env_path=tmp_path / ".env"
    )

    texto = _env(tmp_path)
    assert cofre.tem("OPENROUTER_API_KEY") == CHAVE
    assert CHAVE not in texto and OUTRA not in texto
    assert config_vault.marker("OPENROUTER_API_KEY") in texto
    assert "CHIMERA_CACHE=true" in texto  # the rest of the file is untouched
    assert result["in_vault"] == ["OPENROUTER_API_KEY"]
    # The live process uses the new key at once, as it does when the key goes to the file.
    assert os.environ["OPENROUTER_API_KEY"] == CHAVE


def test_the_marker_is_a_comment_no_reader_of_the_file_takes_for_the_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A placeholder VALUE would outrank the vault (the file is read first) and be sent to the
    provider as the credential. A comment is skipped by every reader."""
    from dotenv import dotenv_values

    _cofre(monkeypatch)
    patch_config(
        {"CHIMERA_KEY_VAULT": "true", "OPENROUTER_API_KEY": CHAVE}, env_path=tmp_path / ".env"
    )

    assert "OPENROUTER_API_KEY" not in dotenv_values(tmp_path / ".env")


def test_the_next_launch_reads_the_key_back_from_the_vault(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The round trip the plan asked to be measured: save in the vault, read on the next launch."""
    _cofre(monkeypatch)
    patch_config(
        {"CHIMERA_KEY_VAULT": "true", "OPENROUTER_API_KEY": CHAVE}, env_path=tmp_path / ".env"
    )

    # A new process: nothing in the environment but what it inherited (empty), and the `.env`.
    monkeypatch.setenv("OPENROUTER_API_KEY", "")
    get_settings.cache_clear()

    assert get_settings().openrouter_api_key == CHAVE


def test_the_screen_shows_where_a_key_lives_and_never_its_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _cofre(monkeypatch)
    patch_config(
        {"CHIMERA_KEY_VAULT": "true", "OPENROUTER_API_KEY": CHAVE}, env_path=tmp_path / ".env"
    )
    get_settings.cache_clear()

    snapshot = read_config(get_settings(), env_path=tmp_path / ".env")

    assert snapshot["vault"] == {
        "enabled": True,
        "available": True,
        "keys": ["OPENROUTER_API_KEY"],
    }
    row = next(p for p in snapshot["providers"] if p["env"] == "OPENROUTER_API_KEY")
    assert row["in_vault"] is True and row["set"] is True
    assert CHAVE not in repr(snapshot)


def test_clearing_a_key_with_the_vault_on_removes_it_from_the_vault(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cofre = _cofre(monkeypatch)
    patch_config(
        {"CHIMERA_KEY_VAULT": "true", "OPENROUTER_API_KEY": CHAVE}, env_path=tmp_path / ".env"
    )

    patch_config({"OPENROUTER_API_KEY": ""}, env_path=tmp_path / ".env")

    assert cofre.tem("OPENROUTER_API_KEY") is None
    assert config_vault.marker("OPENROUTER_API_KEY") not in _env(tmp_path)


def test_a_pool_follows_the_switch_like_a_single_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cofre = _cofre(monkeypatch)
    monkeypatch.setenv("CHIMERA_KEY_VAULT", "true")
    get_settings.cache_clear()

    pool_add("openrouter", CHAVE, env_path=tmp_path / ".env")

    assert cofre.tem("CHIMERA_OPENROUTER_KEYS") == CHAVE
    assert CHAVE not in _env(tmp_path)


# ------------------------------------------------------------------ on, and the vault cannot


def test_on_with_no_vault_the_key_goes_to_the_file_and_the_save_says_so(
    tmp_path: Path,
) -> None:
    result = patch_config(
        {"CHIMERA_KEY_VAULT": "true", "OPENROUTER_API_KEY": CHAVE}, env_path=tmp_path / ".env"
    )

    assert f"OPENROUTER_API_KEY={CHAVE}" in _env(tmp_path)
    assert "in_vault" not in result
    assert result["vault_fallback"] == ["OPENROUTER_API_KEY"]
    assert read_config(Settings(), env_path=tmp_path / ".env")["vault"]["available"] is False


def test_a_vault_that_refuses_fails_the_save_and_changes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The owner chose the vault; writing the key to plain text instead, without asking, would be
    the one outcome they ruled out."""
    _cofre(monkeypatch, recusa=True)
    (tmp_path / ".env").write_text(f"OPENROUTER_API_KEY={OUTRA}\n", "utf-8")
    monkeypatch.setenv("CHIMERA_KEY_VAULT", "true")
    get_settings.cache_clear()

    with pytest.raises(ValueError, match="OPENROUTER_API_KEY") as refused:
        patch_config({"OPENROUTER_API_KEY": CHAVE}, env_path=tmp_path / ".env")

    assert CHAVE not in str(refused.value)
    assert _env(tmp_path) == f"OPENROUTER_API_KEY={OUTRA}\n"
    assert os.environ["OPENROUTER_API_KEY"] == ""


def test_a_refusal_halfway_undoes_what_the_save_had_already_stored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cofre = _cofre(monkeypatch)
    cofre.dados[(config_vault.SERVICE, "OPENAI_API_KEY")] = OUTRA
    real_store = config_vault.store

    def store(name: str, value: str) -> bool:
        return False if name == "TAVILY_API_KEY" else real_store(name, value)

    monkeypatch.setattr(config_vault, "store", store)
    monkeypatch.setenv("CHIMERA_KEY_VAULT", "true")
    get_settings.cache_clear()

    with pytest.raises(ValueError, match="TAVILY_API_KEY"):
        patch_config(
            {"OPENAI_API_KEY": CHAVE, "ANTHROPIC_API_KEY": CHAVE, "TAVILY_API_KEY": CHAVE},
            env_path=tmp_path / ".env",
        )

    assert cofre.tem("OPENAI_API_KEY") == OUTRA  # put back
    assert cofre.tem("ANTHROPIC_API_KEY") is None  # was not there before
    assert _env(tmp_path) == ""


def test_a_vault_that_says_yes_and_keeps_nothing_is_a_refusal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _cofre(monkeypatch, esquece=True)
    (tmp_path / ".env").write_text(f"OPENROUTER_API_KEY={OUTRA}\n", "utf-8")
    monkeypatch.setenv("CHIMERA_KEY_VAULT", "true")
    get_settings.cache_clear()

    with pytest.raises(ValueError):
        patch_config({"OPENROUTER_API_KEY": CHAVE}, env_path=tmp_path / ".env")

    assert OUTRA in _env(tmp_path)  # the working key was not replaced by a marker to nothing


# ------------------------------------------------------------------ a vault that will not let a copy go


def test_a_new_key_the_vault_will_not_free_the_old_copy_for_fails_the_save(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`forget` answers False both for "not there" and for "refused", and its answer was ignored:
    the save confirmed, the marker went, the old copy stayed — read as someone else's from then on,
    never cleaned. All or nothing instead, like a refused store."""
    cofre = _cofre(monkeypatch)
    patch_config(
        {"CHIMERA_KEY_VAULT": "true", "OPENROUTER_API_KEY": CHAVE}, env_path=tmp_path / ".env"
    )
    antes = _env(tmp_path)
    cofre.recusa_apagar = True

    with pytest.raises(ValueError, match="OPENROUTER_API_KEY"):
        patch_config(
            {"CHIMERA_KEY_VAULT": "false", "OPENROUTER_API_KEY": OUTRA}, env_path=tmp_path / ".env"
        )

    assert _env(tmp_path) == antes
    assert cofre.tem("OPENROUTER_API_KEY") == CHAVE


def test_clearing_a_key_the_vault_will_not_forget_fails_instead_of_coming_back_at_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cleared in the file and kept in the vault, the key would be filled back in at the next
    launch — the save said it was gone and the key would be in force again."""
    cofre = _cofre(monkeypatch)
    env = tmp_path / ".env"
    patch_config({"CHIMERA_KEY_VAULT": "true", "OPENROUTER_API_KEY": CHAVE}, env_path=env)
    antes = _env(tmp_path)
    cofre.recusa_apagar = True

    with pytest.raises(ValueError, match="OPENROUTER_API_KEY"):
        patch_config({"OPENROUTER_API_KEY": ""}, env_path=env)

    assert _env(tmp_path) == antes
    assert cofre.tem("OPENROUTER_API_KEY") == CHAVE


def test_the_way_back_names_a_key_whose_vault_copy_would_not_go_and_keeps_it_marked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cofre = _cofre(monkeypatch)
    cofre.dados[(config_vault.SERVICE, "OPENROUTER_API_KEY")] = CHAVE  # moved by this screen
    cofre.dados[(config_vault.SERVICE, "TAVILY_API_KEY")] = OUTRA  # `chimera secrets set`
    marcador = f"{config_vault.marker('OPENROUTER_API_KEY')}\n"
    (tmp_path / ".env").write_text(marcador, "utf-8")
    cofre.recusa_apagar = True

    result = vault_move("file", env_path=tmp_path / ".env")

    assert result["moved"] == []
    assert sorted(result["failed"]) == ["OPENROUTER_API_KEY", "TAVILY_API_KEY"]
    assert _env(tmp_path) == marcador
    assert cofre.tem("OPENROUTER_API_KEY") == CHAVE and cofre.tem("TAVILY_API_KEY") == OUTRA


# ------------------------------------------------------------------ off again, with keys in the vault


def test_off_a_new_value_for_a_moved_key_goes_to_the_file_and_the_stale_copy_goes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cofre = _cofre(monkeypatch)
    patch_config(
        {"CHIMERA_KEY_VAULT": "true", "OPENROUTER_API_KEY": CHAVE}, env_path=tmp_path / ".env"
    )

    patch_config(
        {"CHIMERA_KEY_VAULT": "false", "OPENROUTER_API_KEY": OUTRA}, env_path=tmp_path / ".env"
    )

    texto = _env(tmp_path)
    assert f"OPENROUTER_API_KEY={OUTRA}" in texto
    assert config_vault.marker("OPENROUTER_API_KEY") not in texto
    assert cofre.tem("OPENROUTER_API_KEY") is None


def test_off_a_key_someone_else_put_in_the_vault_is_left_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`chimera secrets set` is a decision the switch does not own."""
    cofre = _cofre(monkeypatch)
    cofre.dados[(config_vault.SERVICE, "OPENROUTER_API_KEY")] = OUTRA

    patch_config({"OPENROUTER_API_KEY": CHAVE}, env_path=tmp_path / ".env")

    assert cofre.tem("OPENROUTER_API_KEY") == OUTRA


# ------------------------------------------------------------------ a key larger than the vault holds

#: A pool of 18 keys shaped like OpenRouter's (~74 characters each): 1349 characters, 2698 bytes in
#: UTF-16 — past the 2560 Windows' Credential Manager keeps per entry.
POOL_GRANDE = ",".join(f"pool-de-teste-nao-chave-{i:02d}-" + "x" * 48 for i in range(18))


def _como_no_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    """The size check, as it runs on Windows — whatever machine runs the suite."""
    real = config_vault.too_large
    monkeypatch.setattr(config_vault, "too_large", lambda value: real(value, platform="win32"))


def test_the_windows_vault_limit_is_counted_in_utf16_bytes() -> None:
    assert config_vault.WINDOWS_BLOB_LIMIT == 2560
    assert config_vault.too_large("a" * 1280, platform="win32") is False
    assert config_vault.too_large("a" * 1281, platform="win32") is True
    assert config_vault.too_large(POOL_GRANDE, platform="win32") is True
    # Keychain and the Secret Service take values far past any credential.
    assert config_vault.too_large(POOL_GRANDE * 10, platform="darwin") is False
    assert config_vault.too_large(POOL_GRANDE * 10, platform="linux") is False


def test_a_value_too_large_for_the_windows_vault_is_refused_for_that_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Not "the vault refused (locked, or the prompt was cancelled)": the owner would go looking
    for a locked keychain that does not exist. Through `PATCH /api/config`, which takes single keys
    (a pool is edited only by add/remove, below) — so the value is one oversized string."""
    cofre = _cofre(monkeypatch)
    _como_no_windows(monkeypatch)
    (tmp_path / ".env").write_text("CHIMERA_CACHE=true\n", "utf-8")

    with pytest.raises(ValueError, match="too large for the Windows Credential Manager") as refused:
        patch_config(
            {"CHIMERA_KEY_VAULT": "true", "OPENROUTER_API_KEY": POOL_GRANDE},
            env_path=tmp_path / ".env",
        )

    assert "locked" not in str(refused.value) and POOL_GRANDE not in str(refused.value)
    assert cofre.escritas == 0
    assert _env(tmp_path) == "CHIMERA_CACHE=true\n"


def test_adding_the_key_that_outgrows_the_windows_vault_says_why(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cofre = _cofre(monkeypatch)
    _como_no_windows(monkeypatch)
    monkeypatch.setenv("CHIMERA_KEY_VAULT", "true")
    monkeypatch.setenv("CHIMERA_OPENROUTER_KEYS", POOL_GRANDE.rsplit(",", 1)[0])
    get_settings.cache_clear()

    with pytest.raises(ValueError, match="too large"):
        pool_add("openrouter", "mais-uma-de-teste-nao-chave-" + "y" * 48, env_path=tmp_path / ".env")

    assert cofre.escritas == 0


def test_a_move_names_a_key_too_large_for_the_vault_and_leaves_it_working(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cofre = _cofre(monkeypatch)
    _como_no_windows(monkeypatch)
    (tmp_path / ".env").write_text(
        f"CHIMERA_OPENROUTER_KEYS={POOL_GRANDE}\nOPENROUTER_API_KEY={CHAVE}\n", "utf-8"
    )
    monkeypatch.setenv("CHIMERA_KEY_VAULT", "true")
    get_settings.cache_clear()

    result = vault_move("vault", env_path=tmp_path / ".env")

    assert result == {
        "moved": ["OPENROUTER_API_KEY"],
        "failed": [],
        "skipped": [],
        "too_large": ["CHIMERA_OPENROUTER_KEYS"],
    }
    assert f"CHIMERA_OPENROUTER_KEYS={POOL_GRANDE}" in _env(tmp_path)
    assert cofre.tem("CHIMERA_OPENROUTER_KEYS") is None


def test_a_refusal_names_the_key_and_guesses_no_cause(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _cofre(monkeypatch, recusa=True)

    with pytest.raises(ValueError, match="OPENROUTER_API_KEY") as refused:
        patch_config(
            {"CHIMERA_KEY_VAULT": "true", "OPENROUTER_API_KEY": CHAVE}, env_path=tmp_path / ".env"
        )

    assert "locked" not in str(refused.value) and "cancelled" not in str(refused.value)
    assert "log" in str(refused.value)


# ------------------------------------------------------------------ the move, both ways


def test_moving_keys_into_the_vault_and_back_restores_the_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cofre = _cofre(monkeypatch)
    original = f"# my keys\nOPENROUTER_API_KEY={CHAVE}\nTAVILY_API_KEY={OUTRA}\nCHIMERA_CACHE=true\n"
    (tmp_path / ".env").write_text(original, "utf-8")
    monkeypatch.setenv("CHIMERA_KEY_VAULT", "true")
    get_settings.cache_clear()

    ida = vault_move("vault", env_path=tmp_path / ".env")

    assert ida == {
        "moved": ["OPENROUTER_API_KEY", "TAVILY_API_KEY"],
        "failed": [],
        "skipped": [],
        "too_large": [],
    }
    texto = _env(tmp_path)
    assert CHAVE not in texto and OUTRA not in texto
    assert cofre.tem("OPENROUTER_API_KEY") == CHAVE and cofre.tem("TAVILY_API_KEY") == OUTRA

    volta = vault_move("file", env_path=tmp_path / ".env")

    assert sorted(volta["moved"]) == ["OPENROUTER_API_KEY", "TAVILY_API_KEY"]
    assert _env(tmp_path) == original
    assert cofre.dados == {}


def test_moving_into_the_vault_removes_every_cleartext_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """dotenv lets the LAST assignment win and reads `export KEY=` as one. A move that replaced only
    the first line would leave a key in cleartext, in force over the vault, while reporting it moved."""
    cofre = _cofre(monkeypatch)
    (tmp_path / ".env").write_text(
        f"OPENROUTER_API_KEY={OUTRA}\nexport OPENROUTER_API_KEY={CHAVE}\n", "utf-8"
    )
    monkeypatch.setenv("CHIMERA_KEY_VAULT", "true")
    get_settings.cache_clear()

    vault_move("vault", env_path=tmp_path / ".env")

    texto = _env(tmp_path)
    assert CHAVE not in texto and OUTRA not in texto
    assert texto.count("OPENROUTER_API_KEY") == 1  # the one marker
    assert cofre.tem("OPENROUTER_API_KEY") == CHAVE  # the value that was in force


def test_a_key_the_vault_refuses_stays_in_the_file_and_is_named(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _cofre(monkeypatch, recusa=True)
    (tmp_path / ".env").write_text(f"OPENROUTER_API_KEY={CHAVE}\n", "utf-8")
    monkeypatch.setenv("CHIMERA_KEY_VAULT", "true")
    get_settings.cache_clear()

    result = vault_move("vault", env_path=tmp_path / ".env")

    assert result["failed"] == ["OPENROUTER_API_KEY"]
    assert _env(tmp_path) == f"OPENROUTER_API_KEY={CHAVE}\n"


def test_moving_into_the_vault_needs_the_switch_and_the_way_back_does_not(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cofre = _cofre(monkeypatch)
    (tmp_path / ".env").write_text(
        f"{config_vault.marker('OPENROUTER_API_KEY')}\n", "utf-8"
    )
    cofre.dados[(config_vault.SERVICE, "OPENROUTER_API_KEY")] = CHAVE

    with pytest.raises(ValueError, match="CHIMERA_KEY_VAULT"):
        vault_move("vault", env_path=tmp_path / ".env")

    assert vault_move("file", env_path=tmp_path / ".env")["moved"] == ["OPENROUTER_API_KEY"]
    assert _env(tmp_path) == f"OPENROUTER_API_KEY={CHAVE}\n"


def test_the_way_back_never_writes_over_a_key_the_file_already_holds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The file's value is the one in force; overwriting it would change which key the app uses."""
    cofre = _cofre(monkeypatch)
    (tmp_path / ".env").write_text(f"OPENROUTER_API_KEY={OUTRA}\n", "utf-8")
    cofre.dados[(config_vault.SERVICE, "OPENROUTER_API_KEY")] = CHAVE

    result = vault_move("file", env_path=tmp_path / ".env")

    assert result == {"moved": [], "failed": [], "skipped": ["OPENROUTER_API_KEY"]}
    assert _env(tmp_path) == f"OPENROUTER_API_KEY={OUTRA}\n"
    assert cofre.tem("OPENROUTER_API_KEY") == CHAVE


def test_the_way_back_reads_each_key_from_the_file_before_the_vault_copy_goes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The screen says each key is read back where it goes before its old copy is removed. On the
    way back, "where it goes" is a file dotenv parses: an unquoted value containing " #" comes
    back cut, and deleting the vault copy then would lose the key. It stays in the vault, the file
    goes back to how it was, and the key is named."""
    cofre = _cofre(monkeypatch)
    cortada = "valor-de-teste #resto-que-o-dotenv-corta"
    cofre.dados[(config_vault.SERVICE, "OPENROUTER_API_KEY")] = cortada  # moved by this screen
    cofre.dados[(config_vault.SERVICE, "TAVILY_API_KEY")] = cortada  # `chimera secrets set`
    cofre.dados[(config_vault.SERVICE, "GROQ_API_KEY")] = CHAVE  # an ordinary key still moves
    marcador = f"{config_vault.marker('OPENROUTER_API_KEY')}\n"
    (tmp_path / ".env").write_text(marcador, "utf-8")

    result = vault_move("file", env_path=tmp_path / ".env")

    assert sorted(result["failed"]) == ["OPENROUTER_API_KEY", "TAVILY_API_KEY"]
    assert result["moved"] == ["GROQ_API_KEY"]
    assert _env(tmp_path) == f"{marcador}GROQ_API_KEY={CHAVE}\n"
    assert cofre.tem("OPENROUTER_API_KEY") == cortada and cofre.tem("TAVILY_API_KEY") == cortada


def test_with_no_vault_neither_move_pretends(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHIMERA_KEY_VAULT", "true")
    get_settings.cache_clear()
    (tmp_path / ".env").write_text(f"OPENROUTER_API_KEY={CHAVE}\n", "utf-8")

    for direction in ("vault", "file"):
        with pytest.raises(ValueError, match="no OS vault"):
            vault_move(direction, env_path=tmp_path / ".env")
    assert _env(tmp_path) == f"OPENROUTER_API_KEY={CHAVE}\n"


# ------------------------------------------------------------------ the frozen desktop build


def test_the_frozen_build_reads_the_vault_only_when_the_file_marks_a_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Before this, the frozen build had no `keyring` and never touched the OS vault. Bundling it so
    the screen can offer the vault must not turn every launch into a keychain read."""
    cofre = _cofre(monkeypatch)
    cofre.dados[(config_vault.SERVICE, "OPENROUTER_API_KEY")] = CHAVE
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    env = tmp_path / ".env"
    env.write_text("CHIMERA_CACHE=true\n", "utf-8")
    # The suite runs with no env file at all (`conftest.py` sets it to None so a developer's own
    # `.env` never leaks into a test). The frozen app reads `.env` in its data folder, its cwd; this
    # test points the setting at the temp one so `get_settings` reads the marker the way the app does.
    monkeypatch.setitem(Settings.model_config, "env_file", str(env))

    assert config_vault.consulted(str(env)) is False
    get_settings.cache_clear()
    assert get_settings().openrouter_api_key in (None, "")
    assert cofre.leituras == 0

    env.write_text(f"{config_vault.marker('OPENROUTER_API_KEY')}\n", "utf-8")
    assert config_vault.consulted(str(env)) is True
    get_settings.cache_clear()
    assert get_settings().openrouter_api_key == CHAVE


def test_the_frozen_build_reads_only_the_keys_its_file_marks_never_the_whole_vault(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The vault's service name is shared with any pip install on the same account. Its owner's
    `chimera secrets set` — a server token, a Stripe key — must not start to apply inside the desktop
    the moment one unrelated key is moved there: the server token alone would turn every tray look
    into a 401, since the shell cannot read the vault. Not even a hand-typed marker brings the token."""
    cofre = _cofre(monkeypatch)
    for name, value in (
        ("OPENROUTER_API_KEY", CHAVE),
        ("CHIMERA_SERVER_TOKEN", OUTRA),
        ("STRIPE_API_KEY", OUTRA),
    ):
        cofre.dados[(config_vault.SERVICE, name)] = value
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    env = tmp_path / ".env"
    env.write_text(
        f"{config_vault.marker('OPENROUTER_API_KEY')}\n{config_vault.marker('CHIMERA_SERVER_TOKEN')}\n",
        "utf-8",
    )
    monkeypatch.setitem(Settings.model_config, "env_file", str(env))
    get_settings.cache_clear()

    settings = get_settings()

    assert settings.openrouter_api_key == CHAVE
    assert settings.server_token in (None, "")
    assert os.environ.get("STRIPE_API_KEY", "") == ""
    assert config_vault.startup_names(str(env)) == ("OPENROUTER_API_KEY",)


def test_a_source_install_reads_the_vault_as_it_always_did() -> None:
    """`chimera secrets set` has promised this since it existed; nothing here takes it back."""
    assert config_vault.consulted(".env") is True
    assert config_vault.startup_names(".env") == config_vault.STORABLE


# ------------------------------------------------------------------ which copy is in force


def _relancar(monkeypatch: pytest.MonkeyPatch, env: Path) -> Settings:
    """A new process reading ``env``, which inherited none of the credentials. Unset, not empty:
    pydantic-settings takes an EMPTY variable over the file, so `""` would test something else."""
    for name in config_vault.STORABLE:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setitem(Settings.model_config, "env_file", str(env))
    get_settings.cache_clear()
    return get_settings()


def test_a_key_the_file_assigns_is_in_force_over_its_vault_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The vault fills gaps. Written into the environment, a vault copy would outrank the file
    (pydantic-settings ranks the environment first) and every edit of that line would be ignored."""
    cofre = _cofre(monkeypatch)
    cofre.dados[(config_vault.SERVICE, "OPENROUTER_API_KEY")] = CHAVE
    env = tmp_path / ".env"
    env.write_text(f"OPENROUTER_API_KEY={OUTRA}\n", "utf-8")

    assert _relancar(monkeypatch, env).openrouter_api_key == OUTRA


def test_after_the_way_back_skips_a_key_the_files_value_is_the_one_in_force(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """"Left in the vault, because .env already holds a value for them" is a claim about which key
    the app uses. Pinned against `get_settings`, not against the file's text."""
    cofre = _cofre(monkeypatch)
    cofre.dados[(config_vault.SERVICE, "OPENROUTER_API_KEY")] = CHAVE
    env = tmp_path / ".env"
    env.write_text(f"OPENROUTER_API_KEY={OUTRA}\n", "utf-8")

    assert vault_move("file", env_path=env)["skipped"] == ["OPENROUTER_API_KEY"]
    assert _relancar(monkeypatch, env).openrouter_api_key == OUTRA


def test_off_a_new_key_saved_over_a_cli_vault_copy_is_the_one_in_force_after_a_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The switch off leaves a `chimera secrets set` copy alone — which is only safe because the
    file's new value outranks it at the next launch, instead of the old key coming back."""
    cofre = _cofre(monkeypatch)
    cofre.dados[(config_vault.SERVICE, "OPENROUTER_API_KEY")] = OUTRA
    env = tmp_path / ".env"

    patch_config({"OPENROUTER_API_KEY": CHAVE}, env_path=env)

    assert cofre.tem("OPENROUTER_API_KEY") == OUTRA
    assert _relancar(monkeypatch, env).openrouter_api_key == CHAVE


def test_a_marker_for_another_service_or_name_is_not_ours() -> None:
    assert config_vault.marker_name(config_vault.marker("OPENROUTER_API_KEY")) == "OPENROUTER_API_KEY"
    assert config_vault.marker_name("# OPENROUTER_API_KEY: in the OS vault (someone-else)") is None
    assert config_vault.marker_name("# CHIMERA_DEFAULT_MODEL: in the OS vault (chimera-agent)") is None


def test_every_credential_the_screen_masks_is_one_the_vault_may_store() -> None:
    """A switch about "the keys" that left five of them in the file would hold for some of them."""
    from chimera.api.config_api import _SECRET_KEYS

    missing = _SECRET_KEYS - set(config_vault.STORABLE)
    assert not missing, sorted(missing)


# ------------------------------------------------------------------ the server token stays in the file


def _token_como_a_bandeja_le(body: str) -> str | None:
    """`token_in_dotenv` in `apps/desktop/src-tauri/src/sidecar_http.rs`, line for line.

    The desktop shell sends `CHIMERA_SERVER_TOKEN` with every tray look and finds it ONLY in its own
    environment or here: comments skipped, an optional `export `, the last assignment wins, the
    name compared case-insensitively. It has no vault. Ported so a Python test can say whether the
    shell still finds the token after this screen has written the file.
    """
    found: str | None = None
    for raw in body.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        line = line.removeprefix("export ")
        name, sep, value = line.partition("=")
        if not sep or name.strip().upper() != "CHIMERA_SERVER_TOKEN":
            continue
        value = value.strip()
        if value[:1] in ('"', "'"):
            value = value[1:].split(value[0])[0]
        else:
            value = value.split(" #")[0].rstrip()
        found = value
    return found or None


def test_the_server_token_is_saved_where_the_desktop_shell_reads_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """In the vault, the token's `.env` line would be our marker — a comment the shell skips — while
    the backend, reading the vault at startup, kept enforcing it: every tray look a 401, pending
    approvals and today's spend gone from the tray, and nothing on screen to show why."""
    cofre = _cofre(monkeypatch)

    result = patch_config(
        {"CHIMERA_KEY_VAULT": "true", "CHIMERA_SERVER_TOKEN": CHAVE, "OPENROUTER_API_KEY": OUTRA},
        env_path=tmp_path / ".env",
    )

    assert result["in_vault"] == ["OPENROUTER_API_KEY"]
    assert cofre.tem("CHIMERA_SERVER_TOKEN") is None
    assert _token_como_a_bandeja_le(_env(tmp_path)) == CHAVE


def test_moving_the_keys_into_the_vault_leaves_the_server_token_in_the_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cofre = _cofre(monkeypatch)
    (tmp_path / ".env").write_text(
        f"CHIMERA_SERVER_TOKEN={CHAVE}\nOPENROUTER_API_KEY={OUTRA}\n", "utf-8"
    )
    monkeypatch.setenv("CHIMERA_KEY_VAULT", "true")
    get_settings.cache_clear()

    result = vault_move("vault", env_path=tmp_path / ".env")

    assert result == {"moved": ["OPENROUTER_API_KEY"], "failed": [], "skipped": [], "too_large": []}
    assert cofre.tem("CHIMERA_SERVER_TOKEN") is None
    assert _token_como_a_bandeja_le(_env(tmp_path)) == CHAVE


def test_the_way_back_brings_out_a_server_token_the_cli_put_in_the_vault(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`chimera secrets set` may still store it — that one is invisible to the tray, and the way
    back is the repair: after it, the shell finds the token again."""
    cofre = _cofre(monkeypatch)
    cofre.dados[(config_vault.SERVICE, "CHIMERA_SERVER_TOKEN")] = CHAVE

    result = vault_move("file", env_path=tmp_path / ".env")

    assert result["moved"] == ["CHIMERA_SERVER_TOKEN"]
    assert _token_como_a_bandeja_le(_env(tmp_path)) == CHAVE


def test_the_screen_may_vault_every_credential_it_masks_but_the_server_token() -> None:
    from chimera.api.config_api import _SECRET_KEYS

    assert set(key_vault.SCREEN_STORABLE) == set(config_vault.STORABLE) - {"CHIMERA_SERVER_TOKEN"}
    assert _SECRET_KEYS - set(key_vault.SCREEN_STORABLE) == {"CHIMERA_SERVER_TOKEN"}


def test_the_shell_reader_port_skips_the_marker_like_the_rust_test_says() -> None:
    """The port is only evidence if it agrees with the Rust tests it copies."""
    assert _token_como_a_bandeja_le("# CHIMERA_SERVER_TOKEN=commented\n") is None
    assert _token_como_a_bandeja_le(config_vault.marker("CHIMERA_SERVER_TOKEN")) is None
    assert _token_como_a_bandeja_le("A=1\nexport chimera_server_token='x y'\n") == "x y"


# ------------------------------------------------------------------ the bridge


def test_the_bridge_can_neither_flip_the_switch_nor_reach_the_move() -> None:
    assert "CHIMERA_KEY_VAULT" in OWNER_ONLY_SETTINGS
    assert not any(route.path.startswith("/api/config/vault") for route in ROUTES.values())


def test_a_bridge_with_full_control_is_refused_the_switch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.test_every_way_into_this_machine_is_on_one_card import _build

    client = _build(
        tmp_path,
        monkeypatch,
        frozen=False,
        CHIMERA_DESKTOP_BRIDGE="true",
        CHIMERA_DESKTOP_BRIDGE_FULL="true",
        CHIMERA_KEY_VAULT="true",
    )
    app: Any = client.app
    bearer = {"Authorization": f"Bearer {app.state.desktop_bridge.token}"}

    refused = client.post(
        "/api/bridge/call",
        json={"route": "settings.edit", "body": {"CHIMERA_KEY_VAULT": "false"}},
        headers=bearer,
    )

    assert refused.status_code == 403
    assert "CHIMERA_KEY_VAULT" not in _env(tmp_path)


def test_the_move_endpoint_answers_in_names(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.test_every_way_into_this_machine_is_on_one_card import _build

    cofre = _cofre(monkeypatch)
    (tmp_path / ".env").write_text(f"OPENROUTER_API_KEY={CHAVE}\n", "utf-8")
    client = _build(tmp_path, monkeypatch, frozen=False)

    off = client.post("/api/config/vault/move", json={"to": "vault"})
    assert off.status_code == 400

    monkeypatch.setenv("CHIMERA_KEY_VAULT", "true")
    get_settings.cache_clear()
    moved = client.post("/api/config/vault/move", json={"to": "vault"})

    assert moved.status_code == 200
    assert moved.json() == {
        "moved": ["OPENROUTER_API_KEY"],
        "failed": [],
        "skipped": [],
        "too_large": [],
    }
    assert CHAVE not in moved.text and CHAVE not in client.get("/api/config").text
    assert cofre.tem("OPENROUTER_API_KEY") == CHAVE
    assert client.post("/api/config/vault/move", json={"to": "elsewhere"}).status_code == 422


def test_a_pool_written_to_the_file_with_the_switch_on_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No vault on this machine and the switch on: the whole pool goes to `.env` in plain text.
    `write_credentials` knew, and the pool write threw the answer away — the screen's only notice
    of a fallback reads a PATCH's answer, never a pool's. Both pool routes now carry it."""
    from tests.test_every_way_into_this_machine_is_on_one_card import _build

    client = _build(tmp_path, monkeypatch, frozen=False, CHIMERA_KEY_VAULT="true")

    added = client.post("/api/config/pool/openrouter", json={"key": CHAVE})
    client.post("/api/config/pool/openrouter", json={"key": OUTRA})
    # A removal writes the rest of the pool back — in plain text, so it says so too. (Removing the
    # LAST key writes nothing secret and says nothing: there is no key left in the file.)
    removed = client.delete("/api/config/pool/openrouter/0")

    for answer in (added, removed):
        assert answer.status_code == 200
        assert answer.json()["vault_fallback"] == ["CHIMERA_OPENROUTER_KEYS"]
        assert answer.json()["in_vault"] == []
        assert CHAVE not in answer.text and OUTRA not in answer.text
    assert client.delete("/api/config/pool/openrouter/0").json()["vault_fallback"] == []


def test_a_pool_written_to_the_vault_says_where_it_went(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _cofre(monkeypatch)
    monkeypatch.setenv("CHIMERA_KEY_VAULT", "true")
    get_settings.cache_clear()

    result = pool_add("openrouter", CHAVE, env_path=tmp_path / ".env")

    assert result == {"provider": "openrouter", "count": 1, "in_vault": ["CHIMERA_OPENROUTER_KEYS"]}


def test_a_save_keeps_the_export_a_sourced_env_needs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """dotenv ignores `export`; a VPS script doing `source .env` does not. Before the vault, a save
    appended `KEY=new` below `export KEY=old` and the shell kept the variable exported; replacing
    the line in place must not quietly drop it, setting or credential, switch off."""
    monkeypatch.setenv("CHIMERA_DEFAULT_MODEL", "")  # patch_config writes os.environ for real
    (tmp_path / ".env").write_text(
        "export CHIMERA_DEFAULT_MODEL=old\nCHIMERA_DEFAULT_MODEL=older\n"
        f"export TAVILY_API_KEY={OUTRA}\n",
        "utf-8",
    )

    patch_config(
        {"CHIMERA_DEFAULT_MODEL": "openrouter/new", "TAVILY_API_KEY": CHAVE},
        env_path=tmp_path / ".env",
    )

    assert _env(tmp_path) == (
        f"export CHIMERA_DEFAULT_MODEL=openrouter/new\nexport TAVILY_API_KEY={CHAVE}\n"
    )


def test_a_marker_never_takes_the_export(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _cofre(monkeypatch)
    (tmp_path / ".env").write_text(f"export OPENROUTER_API_KEY={CHAVE}\n", "utf-8")
    monkeypatch.setenv("CHIMERA_KEY_VAULT", "true")
    get_settings.cache_clear()

    vault_move("vault", env_path=tmp_path / ".env")

    assert _env(tmp_path) == f"{config_vault.marker('OPENROUTER_API_KEY')}\n"


def test_set_env_entry_keeps_everything_else_in_its_place(tmp_path: Path) -> None:
    path = tmp_path / ".env"
    path.write_text("A=1\nOPENROUTER_API_KEY=x\nB=2\nOPENROUTER_API_KEY=y\nC=3\n", "utf-8")

    key_vault.set_env_entry(path, "OPENROUTER_API_KEY", "OPENROUTER_API_KEY=z")

    assert path.read_text("utf-8") == "A=1\nOPENROUTER_API_KEY=z\nB=2\nC=3\n"


# ------------------------------------------------------------------ the frozen build carries a vault


RAIZ = Path(__file__).resolve().parent.parent


def test_the_installer_bundles_keyring_and_the_metadata_its_backends_are_found_by() -> None:
    """Asserted against the recipe, not a built binary: a test cannot freeze one, and a round trip
    in the frozen build would have to touch a real keychain. `keyring` finds its backends through
    entry points, which live in its METADATA — a freeze of the modules alone reports "no vault"
    everywhere, which the app would then honestly say, for a feature it ships."""
    fluxo = (RAIZ / ".github" / "workflows" / "desktop-release.yml").read_text(encoding="utf-8")
    sync = [linha for linha in fluxo.splitlines() if "uv sync --extra" in linha]
    receita = (RAIZ / "apps" / "desktop" / "src-tauri" / "build_sidecar.py").read_text("utf-8")

    assert sync and any("--extra secrets" in linha for linha in sync), sync
    assert '"--collect-all", "keyring", "--copy-metadata", "keyring"' in receita
