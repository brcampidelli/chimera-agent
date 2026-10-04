"""Where the desktop's Settings screen puts a credential: the `.env`, or the OS vault (study 29, P7.7).

The vault itself (`chimera/config_vault.py`) is older than this module. `chimera secrets set` could
always put a key there and `get_settings` has always read it back — but the Settings screen, the one
place most desktop owners ever type a key, wrote every key into `.env` in plain text, readable by any
process running as the owner, including the agent the owner just asked to `cat` something.

This module is the screen's half, behind `CHIMERA_KEY_VAULT` (off by default):

- **On, with a vault:** the key goes into the vault, is read BACK before anything else changes, and
  only then is its `.env` line replaced by a comment saying where it went. A keychain that refuses
  (locked, a cancelled prompt) fails the whole save and puts back what it had overwritten — the
  owner chose the vault, so falling back to plain text without asking would be the one outcome they
  ruled out.
- **On, with no vault** (no `keyring`, a headless box, a frozen build that could not bundle it): the
  key goes to `.env`, as it always did, and the save says so (`vault_fallback`) rather than letting
  a switch that reads "on" describe a file.
- **Off:** the old behaviour — except for a key whose `.env` entry is OUR marker (below), and for
  a key the file assigns more than once: every save, switch on or off, now leaves ONE entry, so the
  value saved is the value in force (dotenv takes the last). An ``export`` on it is kept. A
  marked key lives in the vault because this screen put it there; a new value written to the file
  wins over it anyway (the startup read fills only what neither the environment nor the file sets:
  `config_vault.load_into_environment`), so the stale copy is removed rather than left behind.

Moving existing keys is separate and reversible (:func:`move_to_vault`, :func:`move_to_file`), and
neither direction deletes an original before the copy has been read back.

No function here returns a value. Names, never secrets — the same rule as `config_vault.stored`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from chimera import config_vault

#: The credentials THIS SCREEN may put in the vault: every vault-storable one but the server token.
#:
#: `CHIMERA_SERVER_TOKEN` has a second reader the vault cannot reach. The desktop shell (Rust,
#: `apps/desktop/src-tauri/src/sidecar_http.rs`, `token_now`) sends it with every tray look at
#: `/api/approvals`, `/api/usage` and `/api/code/turns/running`, and finds it only in its own
#: environment or in an UNCOMMENTED `.env` line. Moved to the vault, the file holds our marker — a
#: comment the shell skips — while the backend, which reads the vault at startup, keeps enforcing
#: the token: every tray look turns into a 401, pending approvals and today's spend vanish from the
#: tray, and it tells the owner to fix a token they cannot fix from any screen. The webview keeps
#: working (it gets the token from the page), so nothing on screen would show it. Teaching the shell
#: to read the vault means a keyring crate through the supply-chain audit; until something does,
#: this token stays in the file. `chimera secrets set` may still store it — that is the CLI's
#: owner's decision, and the way back below brings it out like any other.
SCREEN_STORABLE = tuple(
    name for name in config_vault.STORABLE if name != "CHIMERA_SERVER_TOKEN"
)


def _entry_for(line: str, key: str) -> bool:
    """Whether ``line`` is ``key``'s entry: an assignment to it or our vault marker for it.

    Assignments in every spelling python-dotenv reads as one — ``KEY=v``, ``KEY = v``, ``export
    KEY=v`` — because a move into the vault that missed one would leave the key in cleartext in the
    file and in force over the vault copy, while the screen reported it moved.
    """
    text = line.strip()
    if config_vault.marker_name(text) == key:
        return True
    if text.startswith("export "):
        text = text[len("export ") :].lstrip()
    name, sep, _ = text.partition("=")
    return bool(sep) and name.strip() == key


def set_env_entry(path: Path, key: str, line: str, *, keep_export: bool = False) -> None:
    """Make ``line`` the only entry for ``key`` in ``.env``, atomically.

    EVERY entry, not the first: python-dotenv lets the LAST assignment win, so replacing only the
    first `KEY=` line of a file that has two would leave the old key in force — and, on the way into
    the vault, would leave a cleartext copy in the very file the move exists to empty.

    ``keep_export``: when the entry being replaced was ``export KEY=…``, the new assignment keeps
    the ``export``. dotenv ignores it, but a script that does ``source .env`` (a VPS, a cron line)
    does not: dropping it would leave a variable its child processes no longer see. Before this
    module, a save APPENDED ``KEY=new`` below the export line, and a shell keeps a re-assigned
    variable exported, so that script never noticed; replacing the line in place must not change
    that. A marker is a comment and never takes it.
    """
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    hits = [i for i, existing in enumerate(lines) if _entry_for(existing, key)]
    if hits:
        if keep_export and lines[hits[0]].lstrip().startswith("export "):
            line = f"export {line}"
        lines[hits[0]] = line
        for i in reversed(hits[1:]):
            del lines[i]
    else:
        lines.append(line)
    tmp = path.parent / (path.name + ".tmp")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    tmp.replace(path)


def remove_env_entry(path: Path, key: str) -> None:
    """Remove every entry for ``key`` from ``.env`` (assignments and our marker), atomically."""
    if not path.exists():
        return
    lines = [
        line
        for line in path.read_text(encoding="utf-8").splitlines()
        if not _entry_for(line, key)
    ]
    tmp = path.parent / (path.name + ".tmp")
    tmp.write_text("\n".join(lines) + "\n" if lines else "", encoding="utf-8")
    tmp.replace(path)


def write_env_value(path: Path, key: str, value: str) -> None:
    """``KEY=value`` in ``.env`` — the file path every non-vault write takes (``export`` kept)."""
    set_env_entry(path, key, f"{key}={value}", keep_export=True)


def _store_verified(name: str, value: str) -> bool:
    """Store, then read it back. A backend that says yes and keeps nothing is a refusal."""
    return config_vault.store(name, value) and config_vault.read(name) == value


def _forget_verified(name: str) -> bool:
    """Remove the vault copy, then look. ``forget``'s own answer cannot be trusted for this: it is
    False both for "it was not there" and for "the vault refused" (a locked keychain, a denied ACL
    prompt on macOS), and only the first means the copy is gone."""
    config_vault.forget(name)
    return config_vault.read(name) is None


def _put_file_back(path: Path, text: str | None) -> None:
    """Restore ``.env`` to ``text`` (None: it did not exist), atomically."""
    if text is None:
        path.unlink(missing_ok=True)
        return
    tmp = path.parent / (path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def _drop_stale_copies(
    names: list[str], *, path: Path, file_before: str | None, stored: dict[str, str | None]
) -> None:
    """Remove the vault copies a save left stale; if one will not go, undo the whole save.

    A copy that stays is not harmless. When the save CLEARED the key, the file now assigns nothing
    and the startup read fills the gap from the vault: the cleared key comes back at the next launch.
    And with the marker gone, nothing marks the copy as this screen's any more, so it would be
    treated as someone else's and never cleaned. So it is all or nothing, like a refused store:
    copies already removed go back, the file goes back, the stores this save made are undone.
    """
    removed: dict[str, str] = {}
    for name in names:
        old = config_vault.read(name)
        if old is None:
            continue
        if _forget_verified(name):
            removed[name] = old
            continue
        for gone, value in removed.items():
            config_vault.store(gone, value)
        _put_file_back(path, file_before)
        _restore(stored)
        raise ValueError(
            f"the OS vault would not remove its old copy of {name}; nothing was saved (the vault's "
            "own reason, if it gave one, is in the log)"
        )


def _restore(previous: dict[str, str | None]) -> None:
    """Put the vault back the way it was before a save that failed halfway."""
    for name, value in previous.items():
        if value is None:
            config_vault.forget(name)
        else:
            config_vault.store(name, value)


def write_credentials(
    updates: dict[str, str], *, path: Path, vault_on: bool
) -> tuple[list[str], list[str]]:
    """Write the vault-storable credentials in ``updates``; return ``(in_vault, vault_fallback)``.

    Keys outside :data:`SCREEN_STORABLE` are left to the caller, which writes them to the file as
    it always has. Raises ``ValueError`` — naming the key, never the value — when the vault refuses,
    after undoing whatever this call had already stored, and BEFORE storing anything when a value is
    larger than this machine's vault can hold (`config_vault.too_large`): that refusal is certain,
    and reporting it as one more "the vault refused" would send the owner looking for a locked
    keychain that is not there.
    """
    storable = {k: v for k, v in updates.items() if k in SCREEN_STORABLE}
    if not storable:
        return [], []
    available = config_vault.available()
    to_vault = (
        {k: v for k, v in storable.items() if v.strip()} if vault_on and available else {}
    )
    fallback = sorted(k for k, v in storable.items() if v.strip()) if vault_on and not available else []
    marked = set(config_vault.marked(path))

    for name, value in to_vault.items():
        if config_vault.too_large(value):
            raise ValueError(
                f"{name} is too large for the Windows Credential Manager "
                f"({len(value.encode('utf-16-le'))} bytes; it keeps at most "
                f"{config_vault.WINDOWS_BLOB_LIMIT} per entry); nothing was saved. Turn the vault "
                "switch off to keep it in .env, or use fewer keys in the pool"
            )

    previous = {name: config_vault.read(name) for name in to_vault}
    done: dict[str, str | None] = {}
    for name, value in to_vault.items():
        done[name] = previous[name]
        if not _store_verified(name, value):
            _restore(done)
            # No cause is named: the vault's own reason, when it gave one, is what `store` logged,
            # and a guess here ("locked") is how an owner ends up hunting the wrong problem.
            raise ValueError(
                f"the OS vault did not keep {name}; nothing was saved (the vault's own reason, if "
                "it gave one, is in the log)"
            )

    file_before = path.read_text(encoding="utf-8") if path.exists() else None
    stale: list[str] = []
    for name, value in storable.items():
        if name in to_vault:
            set_env_entry(path, name, config_vault.marker(name))
            continue
        write_env_value(path, name, value)
        # Forgotten only AFTER the file holds the new value, and only where the vault copy is ours
        # (a marker) or the owner cleared the key with the vault on. A key put there by `chimera
        # secrets set` is someone else's decision, and the switch being off means "leave the vault
        # alone".
        if name in marked or (vault_on and available and not value.strip()):
            stale.append(name)
    _drop_stale_copies(stale, path=path, file_before=file_before, stored=done)
    return sorted(to_vault), fallback


def move_to_vault(path: Path) -> dict[str, list[str]]:
    """Move every storable key the `.env` holds into the vault. Reversible: :func:`move_to_file`.

    One key at a time, each read back before its line is replaced, so a refusal halfway leaves the
    keys not yet moved exactly where they were — in the file, working — and says which. A key larger
    than this machine's vault can hold is not attempted; it stays in the file and is named apart
    (``too_large``), so "stayed where they were" never stands in for a reason the code knows.
    """
    if not config_vault.available():
        raise ValueError(
            "no OS vault on this machine — the keys stay in .env (install the `secrets` extra on a "
            "desktop with a keychain)"
        )
    from dotenv import dotenv_values

    values = dotenv_values(path) if path.exists() else {}
    moved: list[str] = []
    failed: list[str] = []
    too_large: list[str] = []
    for name in SCREEN_STORABLE:
        value = values.get(name)
        if not value:
            continue
        if config_vault.too_large(value):
            too_large.append(name)
        elif _store_verified(name, value):
            set_env_entry(path, name, config_vault.marker(name))
            moved.append(name)
        else:
            failed.append(name)
    return {"moved": moved, "failed": failed, "skipped": [], "too_large": too_large}


def move_to_file(path: Path) -> dict[str, list[str]]:
    """Move the keys this screen put in the vault back into the `.env` — the way back.

    What moves is what the file marks, plus anything else the vault holds that the file has no
    value for (a key stored with `chimera secrets set` is in force only through the vault, so moving
    "the keys out of the vault" and leaving it behind would not be that). A key the file already
    holds a value for is SKIPPED, vault copy kept: the file's value is the one in force, and
    overwriting it with the vault's would silently change which key the app uses.

    Each key is read back FROM THE FILE, the way dotenv reads it, before its vault copy is removed —
    the same rule as the way in. The write is atomic, but an unquoted `KEY=value` is not every value:
    dotenv cuts at " #" and strips quotes, so a key that does not survive the trip would be lost
    with the vault copy deleted. Such a key is put back as it was (marker, or no line) and named in
    ``failed``, its vault copy kept.
    """
    if not config_vault.available():
        raise ValueError("no OS vault on this machine — there is nothing it could hand back")
    from dotenv import dotenv_values

    values = dotenv_values(path) if path.exists() else {}
    marked = config_vault.marked(path)
    candidates = list(dict.fromkeys([*marked, *config_vault.stored()]))
    moved: list[str] = []
    failed: list[str] = []
    skipped: list[str] = []
    for name in candidates:
        if values.get(name):
            skipped.append(name)
            continue
        value = config_vault.read(name)
        if value is None:
            failed.append(name)
            continue
        write_env_value(path, name, value)
        if dotenv_values(path).get(name) != value:
            if name in marked:
                set_env_entry(path, name, config_vault.marker(name))
            else:
                remove_env_entry(path, name)
            failed.append(name)
            continue
        if not _forget_verified(name):
            # The vault kept its copy (locked, a denied prompt). Reporting the key "moved" would be
            # false, and with its marker gone the copy would read as someone else's and never be
            # cleaned — so the file goes back to marking it, and the key is named.
            if name in marked:
                set_env_entry(path, name, config_vault.marker(name))
            else:
                remove_env_entry(path, name)
            failed.append(name)
            continue
        moved.append(name)
    return {"moved": moved, "failed": failed, "skipped": skipped}


def vault_snapshot(*, enabled: bool, path: Path) -> dict[str, Any]:
    """The Settings screen's view of the vault: on or off, present or absent, which NAMES it holds.

    The vault is asked what it holds only when the switch is on or the file marks a key — the same
    condition under which the frozen build reads it at startup — so an owner who never opted in
    gets no keychain access from opening Settings either.
    """
    available = config_vault.available()
    asks = available and (enabled or bool(config_vault.marked(path)))
    return {
        "enabled": enabled,
        "available": available,
        "keys": config_vault.stored() if asks else [],
    }
