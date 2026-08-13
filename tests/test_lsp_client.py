"""Driving a real `ruff server` (:mod:`chimera.lsp.client`).

The server is the actual binary, not a fake. A fake language server would be a second implementation
of my own understanding of the protocol, and every place my understanding is wrong it would agree
with me — which is the one property a test must not have. `ruff` is a dev-dependency of this
project, so it is present wherever these tests run; where it is somehow not, they skip and say so
rather than passing.
"""

from __future__ import annotations

import shutil
import time
from pathlib import Path

import pytest

from chimera.lsp.client import RuffClient, path_to_uri, uri_to_path
from chimera.lsp.positions import from_utf16_column

pytestmark = pytest.mark.skipif(shutil.which("ruff") is None, reason="ruff not on PATH")


def _wait_for(client: RuffClient, path: Path, timeout: float = 30.0) -> list:
    """Diagnostics arrive as a notification, so there is nothing to await — only to watch for."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        found = client.diagnostics_for(path)
        if found:
            return found
        time.sleep(0.05)
    return client.diagnostics_for(path)


@pytest.fixture()
def client(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text(
        '[tool.ruff]\nline-length = 100\n[tool.ruff.lint]\nselect = ["E", "F"]\n', encoding="utf-8"
    )
    running = RuffClient(tmp_path).start()
    yield running
    running.close()


def test_it_reports_a_real_problem(client: RuffClient, tmp_path: Path) -> None:
    """End to end against the binary: an unused import is F401, which is also what CI would say."""
    path = tmp_path / "app.py"
    source = "import os\n\nx = 1\n"
    path.write_text(source, encoding="utf-8")

    client.open_document(path, source)
    found = _wait_for(client, path)

    assert found, "ruff reported nothing for an unused import"
    assert any(d.code == "F401" for d in found)
    assert all(d.severity in ("error", "warning", "information", "hint") for d in found)


def test_a_clean_file_reports_nothing(client: RuffClient, tmp_path: Path) -> None:
    # And "nothing" has to be distinguishable from "we never asked": the diagnostics arrive as an
    # empty list rather than never arriving.
    path = tmp_path / "clean.py"
    source = "x = 1\n"
    path.write_text(source, encoding="utf-8")

    client.open_document(path, source)
    time.sleep(1.5)

    assert client.diagnostics_for(path) == []


def test_the_column_is_right_when_an_astral_character_precedes_the_problem(
    client: RuffClient, tmp_path: Path
) -> None:
    """The reason :mod:`chimera.lsp.positions` exists, against the real server.

    The emoji and the problem must be on the SAME line — a first version put the emoji on line 0 and
    the import on line 1, where the conversion is the identity, so it tested nothing while claiming
    to test this.

    Measured here: the line is 23 Python characters and 24 UTF-16 units, and ruff reports
    `F401` at column 22. Read as a Python index that is `"s"`; converted it is `"os"`. All three
    diagnostics on the line are off by exactly one, which is the size of the emoji's second
    surrogate — the whole bug, one character wide, in a file nobody writes tests with.
    """
    path = tmp_path / "emoji.py"
    source = 'MSG = "🙂 漢字"; import os\n'
    path.write_text(source, encoding="utf-8")

    client.open_document(path, source)
    found = _wait_for(client, path)

    unused = next((d for d in found if d.code == "F401"), None)
    assert unused is not None, f"expected F401, got {found}"

    line_text = source.splitlines()[unused.line]
    index = from_utf16_column(line_text, unused.column)
    assert line_text[index:] == "os", f"column {unused.column} → index {index}"
    # And the naive reading is wrong, so this test cannot pass by accident on an ASCII-only line.
    assert line_text[unused.column :] != "os"


def test_a_change_replaces_the_previous_diagnostics(client: RuffClient, tmp_path: Path) -> None:
    """The property whole-document sync buys: the server's view cannot drift from ours.

    Fixing the file must clear the squiggle, and a client that desynchronised would keep showing a
    problem in text that no longer exists.
    """
    path = tmp_path / "fix.py"
    broken = "import os\n\nx = 1\n"
    path.write_text(broken, encoding="utf-8")
    client.open_document(path, broken)
    assert _wait_for(client, path), "no diagnostics to clear"

    client.change_document(path, "x = 1\n")

    deadline = time.monotonic() + 30
    while time.monotonic() < deadline and client.diagnostics_for(path):
        time.sleep(0.05)
    assert client.diagnostics_for(path) == []


def test_a_version_only_ever_increases(client: RuffClient, tmp_path: Path) -> None:
    # The protocol requires it, and a server that sees a version go backwards is entitled to ignore
    # the edit — which presents as an editor whose diagnostics stop updating after a while.
    path = tmp_path / "v.py"
    client.open_document(path, "x = 1\n")
    seen = [client._versions[path_to_uri(path)]]
    for _ in range(3):
        client.change_document(path, "y = 2\n")
        seen.append(client._versions[path_to_uri(path)])
    assert seen == sorted(seen) and len(set(seen)) == len(seen)


def test_closing_a_document_forgets_its_diagnostics(client: RuffClient, tmp_path: Path) -> None:
    # Otherwise a file closed with errors keeps reporting them, and a panel that lists "problems in
    # the project" counts a file nobody has open.
    path = tmp_path / "gone.py"
    source = "import os\n"
    client.open_document(path, source)
    _wait_for(client, path)

    client.close_document(path)

    assert client.diagnostics_for(path) == []


def test_the_server_is_alive_until_it_is_closed(client: RuffClient) -> None:
    assert client.is_alive()


def test_closing_twice_is_not_an_error(tmp_path: Path) -> None:
    running = RuffClient(tmp_path).start()
    running.close()
    running.close()
    assert not running.is_alive()


# --- URIs, which every hand-rolled version gets wrong on Windows ------------------------------


def test_a_path_round_trips_through_a_file_uri(tmp_path: Path) -> None:
    """A Windows path needs a drive letter, three slashes and percent-encoding for spaces."""
    path = tmp_path / "a folder" / "app.py"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x = 1\n", encoding="utf-8")

    uri = path_to_uri(path)

    assert uri.startswith("file:///")
    assert "%20" in uri  # the space survived as an escape rather than as a raw space
    assert Path(uri_to_path(uri)) == path.resolve()


def test_a_non_file_uri_is_left_alone() -> None:
    assert uri_to_path("untitled:Untitled-1") == "untitled:Untitled-1"
