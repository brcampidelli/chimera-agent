"""Which governance mode a home gets when nobody chose one - decided once, on its first boot.

Audit P0-15, part 2 (owner decision of 2026-11-14). ``CHIMERA_GOVERNANCE`` shipped ``off``: the trust
kernel and the taint ledger judged nothing on ``serve``, cron, MCP, A2A and the bots, because turning
them on changes what a running deployment may do and nobody should get that from an upgrade. That
reasoning is about EXISTING installs. A NEW one has nothing to protect from a change, so it starts in
``observe`` - the whole stack runs, no approval is asked, nothing ``enforce`` would send to review
is refused (it is recorded: ``chimera report governance`` reads it back). **The fixed signatures
(BLOCK: ``rm -rf /``, ``mkfs``, curl-into-a-shell, ...) are refused in every mode that installs the
kernel, so a new install refuses them where ``off`` did not** - the owner's hard blocks hold in any
mode.

**What "new" means, and why it is a file.** A home is new only when it does not exist or is
empty: ANY earlier entry (memory.db, memory.json, .env, skills/, ...) makes it existing, because
guessing wrong in the other direction switches a running install without telling it. That test is only true ONCE: the first boot writes
``audit.jsonl`` and the next boot would call the same home "existing". So the verdict is written to
``governance_default.json`` the first time a NEW home is seen, and every later boot reads it back. The write
is exclusive (``open(..., "x")``), so two processes starting together agree on the first one's answer.

**What it never does**

* It never overrides a choice. A mode in the environment or in the home's ``.env`` is in
  ``Settings.model_fields_set`` and wins - unless it is EMPTY (``CHIMERA_GOVERNANCE=`` is what a
  copied ``.env.example`` holds), which counts as not configured; the verdict is still recorded, so removing the variable
  later does not turn a new home into an "existing" one.
* It never changes an existing install. A home with prior state and no recorded verdict keeps
  today's default (``off``) - nothing is written for it, its own files already make the answer
  stable - plus a notice - in the log and, on the desktop,
  on the Settings screen - saying what ``observe`` is and how to turn it on.
* It never fails a boot. A home that cannot be written to gets ``off``, the old behaviour, and a log
  line: a verdict that cannot be stored cannot be stable, and an unstable default is worse than none.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import threading
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

log = logging.getLogger(__name__)

#: Where the verdict lives, inside the home.
STATE_FILE = "governance_default.json"

NEW_INSTALL = "new-install"
EXISTING_INSTALL = "existing-install"
UNWRITABLE = "unwritable-home"

Mode = Literal["off", "observe"]


@dataclass(frozen=True)
class FirstBoot:
    """The verdict for a home: the default mode and why."""

    default: Mode
    reason: str

    @property
    def notice(self) -> bool:
        """An existing install, left on ``off``: the one case that earns the notice."""
        return self.reason == EXISTING_INSTALL and self.default == "off"


NOTICE_TEXT = (
    "Governance is OFF on this existing install, so serve, cron, MCP, A2A and the bots run without "
    "the trust kernel. New installs start in `observe`, which asks nothing and does not refuse what "
    "`enforce` would send to review - it records it - while the fixed hard blocks (rm -rf /, mkfs, ...) "
    "refuse in every mode but off. To try it here: set CHIMERA_GOVERNANCE=observe, then read "
    "`chimera report governance`. Nothing was changed for you."
)


def _read(path: Path) -> FirstBoot | None:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict) or raw.get("default") not in ("off", "observe"):
        return None
    return FirstBoot(default=raw["default"], reason=str(raw.get("reason") or ""))


def _is_existing(home: Path) -> bool:
    """Anything in the home but our own verdict (or its temporary) means someone used it before."""
    try:
        return any(not entry.name.startswith(STATE_FILE) for entry in home.iterdir())
    except FileNotFoundError:
        return False
    except OSError:
        return True  # cannot even list it: not provably new, so do not switch it


def _write_exclusive(path: Path, verdict: FirstBoot) -> bool:
    """Publish the verdict whole, if nobody has yet. ``False`` means the home cannot hold it.

    Written to a temporary named for this write alone (process, thread and a random part, so two
    threads of one process never share it) and published with ``os.link``, which fails if the target
    exists: a reader sees no file or the complete one, and two writers agree on the first. Where
    hard links are not available the target is created with ``O_CREAT | O_EXCL`` and written
    directly; a reader that meets that file empty or half-written treats it as "no verdict"
    (``_read``), so it is never an error.
    """
    body = json.dumps(
        {
            "version": 1,
            "default": verdict.default,
            "reason": verdict.reason,
            "decided_at": datetime.now(UTC).isoformat(timespec="seconds"),
        },
        indent=2,
    ) + "\n"
    temporary = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.{uuid.uuid4().hex}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            temporary.write_text(body, encoding="utf-8")
            os.link(temporary, path)
            return True
        except FileExistsError:
            return True  # another writer won the race; the caller reads its verdict back
        except (OSError, NotImplementedError, AttributeError):
            pass  # no hard links here (or no temporary): fall through to the exclusive create
        return _create_exclusive(path, body)
    except OSError:
        return False
    finally:
        with contextlib.suppress(OSError):  # a leftover ``governance_default.json.*.tmp`` is ignored
            temporary.unlink(missing_ok=True)


def _create_exclusive(path: Path, body: str) -> bool:
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    except FileExistsError:
        return True
    except OSError:
        return False
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(body)
    except OSError:
        with contextlib.suppress(OSError):  # do not leave a partial verdict behind
            path.unlink(missing_ok=True)
        return False
    return True


_memo: dict[str, FirstBoot] = {}


def forget() -> None:
    """Drop the in-process memo (tests that rebuild a home; a process never needs it)."""
    global _announced
    _memo.clear()
    _announced = False


def resolve(home: Path) -> FirstBoot:
    """The verdict for ``home``: the recorded one, or the one taken now and recorded.

    Memoised per process: ``get_settings()`` is called by every command and the answer for a home
    cannot change during a run, so after the first call this is a dictionary lookup, not a read.
    """
    key = str(home)
    cached = _memo.get(key)
    if cached is None:
        cached = _memo[key] = _resolve(home)
    return cached


def _resolve(home: Path) -> FirstBoot:
    path = home / STATE_FILE
    recorded = _read(path)
    if recorded is not None:
        return recorded
    if _is_existing(home):
        # Not written: the files that make it existing stay, so the answer is stable without a file,
        # and a read-only command (`chimera report governance`) must not leave one behind.
        return FirstBoot("off", EXISTING_INSTALL)
    verdict = FirstBoot("observe", NEW_INSTALL)
    if not _write_exclusive(path, verdict):
        # Quiet on purpose: this runs inside get_settings(), including in read-only commands, and a
        # home that cannot be written to is simply left as it always was.
        log.debug("governance default for %s could not be recorded; leaving governance off", home)
        return FirstBoot("off", UNWRITABLE)
    return _read(path) or verdict


def apply(settings: Any) -> FirstBoot:
    """Apply the verdict to a freshly built ``Settings`` when the owner has not chosen a mode.

    Assigning marks the field as set, which is what the desktop's own default
    (``_kernel_observes_unless_told_otherwise``) reads as "already chosen" - so a new home is
    ``observe`` on every surface and an existing one is left for that function to decide as before.
    """
    verdict = resolve(Path(settings.home))
    # An EMPTY value is not a choice: `CHIMERA_GOVERNANCE=` is what a copied .env.example holds.
    chosen = "governance_mode" in settings.model_fields_set and bool(
        str(settings.governance_mode).strip()
    )
    if not chosen and verdict.default == "observe":
        settings.governance_mode = "observe"
    return verdict


_announced = False


def announce_existing_install(settings: Any, *, desktop: bool = False) -> bool:
    """Say once, in the process log, that this existing install is left on ``off``.

    Called ONLY by the long-lived surfaces (``serve`` and the desktop app). It is not part of
    ``get_settings()``: that runs in every command, and a warning there landed in the JSON a command
    prints, in ``chimera logs`` and in every script that parses output. The other place the owner is
    told is the Settings screen (``autonomy.governance_notice``).

    A mode the owner chose ends it: a non-empty value in the settings or, for the desktop (which
    starts itself in ``observe``, not a choice), one written to a ``.env`` file.
    """
    global _announced
    if _announced:
        return False
    verdict = resolve(Path(settings.home))
    if desktop:
        from chimera.config import config_env_files

        owner_chose = _env_file_chooses(config_env_files())
    else:
        owner_chose = "governance_mode" in settings.model_fields_set and bool(
            str(settings.governance_mode).strip()
        )
    if not verdict.notice or owner_chose:
        return False
    _announced = True
    log.warning(NOTICE_TEXT)
    return True


def _env_file_chooses(paths: tuple[Path, ...]) -> bool:
    for path in paths:
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            name, sep, value = line.strip().partition("=")
            if sep and name.strip().removeprefix("export ").strip().upper() == "CHIMERA_GOVERNANCE" and value.strip():
                return True
    return False


def notice_active(home: Path, *, env_files: tuple[Path, ...] = ()) -> bool:
    """Whether the desktop shows the notice: an existing install left on ``off`` that never chose.

    The mode the running process holds is deliberately NOT consulted: the desktop starts itself in
    ``observe`` when unchosen, but the unattended surfaces on the same home (serve, cron, bots) are
    the ones this notice is about, and they are still ``off``. A mode written to a ``.env`` file in
    ``env_files`` - which is what the Settings screen writes - is the owner's choice and ends it.
    """
    recorded = _read(home / STATE_FILE)
    if recorded is None:
        recorded = FirstBoot("off", EXISTING_INSTALL) if _is_existing(home) else None
    return recorded is not None and recorded.notice and not _env_file_chooses(env_files)
