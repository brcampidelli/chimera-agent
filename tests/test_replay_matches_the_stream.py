"""A replayed frame has to be the same shape as a streamed one, or replay renders nothing.

The design was written down in the desktop's own `resume.ts`: *the frames go through the SAME
reducer the live stream feeds*. They did not.

The SSE client builds each frame as `{seq, kind, task_id, text, data}` — `kind` is the event name,
`data` is everything else. The transcript on disk is written as `{"event": event, **payload}`:
`event` instead of `kind`, and the payload flattened rather than nested. `GET /runs/{id}` handed
those raw dicts straight back, so every replayed frame reached a reducer that switches on
`frame.kind` with `frame.kind === undefined`.

What that looked like on screen: a run replayed nine frames and drew a stepper — which renders its
labels unconditionally — and **zero worker cards, and no answer**. Measured on rc14 against a real
transcript from the day before.

So the whole persistence feature has been a shell since rc11: the transcripts were written, the
list was there once it was wired, and reading one back produced an empty run.

Normalised at the API boundary rather than on disk. Transcripts already written keep working — they
are also a debugging artefact and rewriting the format would strand every one of them — and the
contract becomes the one the client was promised: this endpoint returns what the stream returns.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("sse_starlette")

from fastapi.testclient import TestClient  # noqa: E402

from chimera.config import Settings  # noqa: E402
from chimera.core.agent import Agent, AgentConfig  # noqa: E402
from chimera.interface import ChatSession  # noqa: E402
from chimera.orchestration import runlog  # noqa: E402
from chimera.tools.registry import ToolRegistry  # noqa: E402

#: The fields the SSE client lifts OUT of the payload and onto the frame itself. Everything else
#: belongs under `data` — the same split `api.ts` performs, kept in one place so the two cannot
#: drift again without a test noticing.
TOP_LEVEL = {"seq", "kind", "task_id", "text"}


def _client(tmp_path: Path) -> tuple[TestClient, Path]:
    from chimera.api import build_api_app

    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    home = tmp_path / "home"
    settings = Settings(CHIMERA_HOME=str(home))
    agent = Agent(None, ToolRegistry(), AgentConfig())
    app = build_api_app(lambda: ChatSession(agent), workspace=ws, settings=settings)
    return TestClient(app), home


def _a_real_run(home: Path) -> str:
    """The exact frames a two-worker fan-out writes, with the fields it really carries."""
    run_id = "abc123"
    runlog.append(home, run_id, "run", {"seq": 1, "run_id": run_id, "task": "compare A and B"})
    runlog.append(home, run_id, "classified", {"seq": 2, "task_id": "", "text": "parallel_read",
                                               "shape": "parallel_read", "sources": 2})
    runlog.append(home, run_id, "worker_started", {"seq": 3, "task_id": "sub-1", "text": "read A",
                                                  "tier": "mid"})
    runlog.append(home, run_id, "worker_verified", {"seq": 4, "task_id": "sub-1",
                                                   "text": "verified", "tokens": 11369})
    runlog.append(home, run_id, "done", {"seq": 5, "task_id": "", "text": "synthesised",
                                        "answer": "A and B differ in one place."})
    return run_id


def test_every_replayed_frame_carries_kind(tmp_path: Path) -> None:
    client, home = _client(tmp_path)
    run_id = _a_real_run(home)

    frames = client.get(f"/api/orchestration/runs/{run_id}?since=0").json()["frames"]

    assert len(frames) == 5
    assert [f["kind"] for f in frames] == [
        "run", "classified", "worker_started", "worker_verified", "done"
    ]


def test_the_routing_key_survives_the_round_trip(tmp_path: Path) -> None:
    # `task_id` is what puts a frame on a worker card. Losing it is losing the cards.
    client, home = _client(tmp_path)
    run_id = _a_real_run(home)

    frames = client.get(f"/api/orchestration/runs/{run_id}?since=0").json()["frames"]
    started = next(f for f in frames if f["kind"] == "worker_started")

    assert started["task_id"] == "sub-1"
    assert started["text"] == "read A"


def test_the_rest_of_the_payload_arrives_under_data(tmp_path: Path) -> None:
    client, home = _client(tmp_path)
    run_id = _a_real_run(home)

    frames = client.get(f"/api/orchestration/runs/{run_id}?since=0").json()["frames"]
    done = next(f for f in frames if f["kind"] == "done")

    assert done["data"]["answer"] == "A and B differ in one place."
    # And NOT left at the top level, where the reducer does not look for it.
    assert "answer" not in done


def test_a_replayed_frame_has_exactly_the_streamed_shape(tmp_path: Path) -> None:
    # The strongest form of the claim, and the one that keeps the two from drifting apart again:
    # a frame off this endpoint has the same keys as a frame off the stream, no more and no fewer.
    client, home = _client(tmp_path)
    run_id = _a_real_run(home)

    frames = client.get(f"/api/orchestration/runs/{run_id}?since=0").json()["frames"]

    for frame in frames:
        assert set(frame) == TOP_LEVEL | {"data"}, f"{frame['kind']} has the wrong keys"
        assert isinstance(frame["data"], dict)
        assert isinstance(frame["seq"], int)


def test_since_still_skips_what_the_client_already_has(tmp_path: Path) -> None:
    # Normalising must not disturb the property that makes a second replay cheap.
    client, home = _client(tmp_path)
    run_id = _a_real_run(home)

    body = client.get(f"/api/orchestration/runs/{run_id}?since=3").json()

    assert [f["seq"] for f in body["frames"]] == [4, 5]
    assert body["seq"] == 5


def test_a_frame_that_never_had_a_task_id_gets_an_empty_one(tmp_path: Path) -> None:
    # Real, not hypothetical: the `run` frame carries `run_id` and `task` and no `task_id` at all,
    # and so do `classified`, `synthesizing` and `done` in some paths. Handing the reducer a frame
    # missing the key it routes on would be a different way of losing the same cards.
    #
    # A missing `seq` is NOT tested, because no path can produce one: both callers of
    # `runlog.append` stamp it before writing. A test for a state the code cannot reach describes
    # nothing and would have to be maintained anyway.
    client, home = _client(tmp_path)
    run_id = _a_real_run(home)

    frames = client.get(f"/api/orchestration/runs/{run_id}?since=0").json()["frames"]
    first = frames[0]

    assert first["kind"] == "run"
    assert first["task_id"] == ""
    assert first["data"]["task"] == "compare A and B"


def test_a_line_with_no_event_at_all_is_dropped_rather_than_renamed(tmp_path: Path) -> None:
    # Garbage in the file should not become a frame with an empty `kind`, which the reducer would
    # accept and silently ignore. Dropping it is the honest reading of "this is not a frame".
    run_id = "damaged"
    client, home = _client(tmp_path)
    _a_real_run(home)  # a healthy run, so the assertion is not about an empty file
    directory = runlog.run_dir(home, run_id)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "frames.jsonl").write_text(
        '{"seq": 1, "note": "not a frame"}\n{"event": "done", "seq": 2}\n', encoding="utf-8"
    )

    frames = client.get(f"/api/orchestration/runs/{run_id}?since=0").json()["frames"]

    assert [f["kind"] for f in frames] == ["done"]
