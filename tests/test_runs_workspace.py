"""A receipt records which project it came from, and both readers can ask for one.

Without this, Runs and "Was it worth it?" can only be global — and a panel that pools runs from
three projects into one verdict is worse than no panel, because the number still looks like an
answer to "was this configuration worth it here".
"""

from __future__ import annotations

import json
from pathlib import Path

from chimera.api.runs import RunReceipt, append_run, load_runs


def _write(home: Path, *receipts: RunReceipt) -> Path:
    path = home / "runs.jsonl"
    for receipt in receipts:
        append_run(path, receipt)
    return path


def test_a_receipt_carries_its_project(tmp_path: Path) -> None:
    path = _write(tmp_path, RunReceipt(ts="t", task="a", workspace="/passapro"))
    assert load_runs(path)[0].workspace == "/passapro"


def test_unfiltered_stays_everything(tmp_path: Path) -> None:
    """Every existing caller means "all of them", and this endpoint has always answered that."""
    path = _write(
        tmp_path,
        RunReceipt(ts="1", task="a", workspace="/one"),
        RunReceipt(ts="2", task="b", workspace="/two"),
    )
    assert len(load_runs(path)) == 2


def test_filtering_returns_only_that_project(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        RunReceipt(ts="1", task="a", workspace="/one"),
        RunReceipt(ts="2", task="b", workspace="/two"),
        RunReceipt(ts="3", task="c", workspace="/one"),
    )
    assert [r.task for r in load_runs(path, workspace="/one")] == ["a", "c"]


def test_a_receipt_with_no_project_is_never_attributed_to_one(tmp_path: Path) -> None:
    """The whole reason the field defaults to empty rather than to something plausible.

    A receipt written before this existed belongs to a project nobody recorded. Showing it under
    whichever project happens to be open would put fabricated evidence into the two views built to
    judge what happened where — and it would fabricate a DIFFERENT answer for each reader.
    """
    path = _write(
        tmp_path,
        RunReceipt(ts="1", task="old"),  # no workspace: predates the field
        RunReceipt(ts="2", task="new", workspace="/one"),
    )
    assert [r.task for r in load_runs(path, workspace="/one")] == ["new"]
    assert [r.task for r in load_runs(path, workspace="")] == ["old"]  # findable, deliberately
    assert len(load_runs(path)) == 2  # and never lost


def test_an_old_receipt_still_loads(tmp_path: Path) -> None:
    """The log is append-only and predates the field, so every line written before today lacks it.
    A reader that rejected those would empty the Runs screen on upgrade."""
    path = tmp_path / "runs.jsonl"
    path.write_text(
        json.dumps({"ts": "1", "task": "before the field", "success": True}) + "\n",
        encoding="utf-8",
    )
    loaded = load_runs(path)
    assert len(loaded) == 1
    assert loaded[0].workspace == ""


def test_the_run_endpoint_narrows_to_a_project(tmp_path: Path) -> None:
    """Asserted through the API, because the filter is only worth anything if it is reachable."""
    from fastapi.testclient import TestClient

    from chimera.api.app import build_api_app
    from chimera.config import Settings

    settings = Settings(CHIMERA_HOME=str(tmp_path))
    _write(
        tmp_path,
        RunReceipt(ts="1", task="a", workspace="/one"),
        RunReceipt(ts="2", task="b", workspace="/two"),
    )
    client = TestClient(build_api_app(lambda: None, settings=settings))  # type: ignore[arg-type]

    assert len(client.get("/api/runs").json()) == 2
    narrowed = client.get("/api/runs", params={"workspace": "/one"}).json()
    assert [r["task"] for r in narrowed] == ["a"]
    assert narrowed[0]["workspace"] == "/one"  # labelled, not only filtered
