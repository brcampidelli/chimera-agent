"""~27 artefacts under the home and not one of them said which version wrote it.

    $ grep -rn "schema_version|migration_version|first_run|last_version|PRAGMA user_version" chimera/
    (nothing)

So the process could not detect it was reading an older layout — every question about an upgrade was
answered by guessing, and a guess is indistinguishable from a bug to whoever is asking.

This does not migrate anything, and that is deliberate. The value is being able to SAY "this home
was created by 0.31 and you are on 0.48". A migration registry belongs here later; building one now
would be scaffolding around a house with no door. What already exists — the tolerant readers, the
optional field with an honest default, the column check in `memory/sqlite_store.py` — is the right
pattern and stays where it is.
"""

from __future__ import annotations

import json
from pathlib import Path

from chimera.core.state_version import FILENAME, STATE_VERSION, read, stamp


def test_a_home_nobody_stamped_says_so(tmp_path: Path) -> None:
    """`unknown` is its own answer. "Created before we recorded it" and "created by the first
    version that did" are different facts, and reporting the first as the second invents evidence
    about a machine nobody looked at."""
    marca = read(tmp_path)

    assert marca.known is False
    assert marca.chimera_version == ""
    assert marca.state_version == 0


def test_stamping_records_this_version(tmp_path: Path) -> None:
    from chimera import __version__

    stamp(tmp_path)

    marca = read(tmp_path)
    assert marca.known is True
    assert marca.chimera_version == __version__
    assert marca.state_version == STATE_VERSION


def test_stamping_returns_what_was_there_before(tmp_path: Path) -> None:
    """The question worth answering is "what changed", and that needs the previous value — which is
    gone the moment the new one is written."""
    (tmp_path / FILENAME).write_text(
        json.dumps({"chimera_version": "0.31.0", "state_version": 1}), encoding="utf-8"
    )

    anterior = stamp(tmp_path)

    assert anterior.chimera_version == "0.31.0"
    assert read(tmp_path).chimera_version != "0.31.0"


def test_it_records_the_LAST_version_to_touch_the_home(tmp_path: Path) -> None:
    """Written unconditionally, not only when absent: a home carried through five releases that
    records only the first answers a question nobody asked."""
    (tmp_path / FILENAME).write_text(
        json.dumps({"chimera_version": "0.31.0", "state_version": 1}), encoding="utf-8"
    )

    stamp(tmp_path)
    stamp(tmp_path)

    assert read(tmp_path).chimera_version != "0.31.0"


# ------------------------------------------------------------------ it never gets in the way


def test_a_corrupt_stamp_reads_as_unknown(tmp_path: Path) -> None:
    """This file is bookkeeping. A process that will not start because a bookkeeping file is
    unreadable has turned a note into a dependency."""
    (tmp_path / FILENAME).write_text("{isto nao e json", encoding="utf-8")

    assert read(tmp_path).known is False


def test_a_stamp_of_the_wrong_shape_reads_as_unknown(tmp_path: Path) -> None:
    (tmp_path / FILENAME).write_text(json.dumps(["uma lista"]), encoding="utf-8")

    assert read(tmp_path).known is False


def test_stamping_an_unwritable_place_does_not_raise(tmp_path: Path) -> None:
    """Same rule from the other side: a home that cannot be stamped keeps working without a stamp."""
    ocupado = tmp_path / "arquivo"
    ocupado.write_text("nao sou uma pasta", encoding="utf-8")

    stamp(ocupado)  # must not raise

    assert read(ocupado).known is False


# ------------------------------------------------------------------ it is actually called


def test_the_doctor_stamps_and_reports(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """The wiring, asserted — because a module nothing calls is the exact defect this fixes.

    `doctor` is the right caller: its job is to know the state of this machine, and stamping from a
    library import would write to disk on `import chimera`.
    """
    from typer.testing import CliRunner

    from chimera.cli.main import app
    from chimera.config import get_settings

    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path))
    get_settings.cache_clear()
    try:
        saida = CliRunner().invoke(app, ["doctor"])

        assert "State written by" in saida.stdout
        assert read(tmp_path).known is True
    finally:
        get_settings.cache_clear()
