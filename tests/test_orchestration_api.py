"""The orchestration HTTP surface. No provider key, no network — the gateway is injected.

What these protect, in order of how much it would cost to get wrong:

- the preview must not run anything (a "see the plan" button that spends is not a preview);
- the frame order and the sequence numbers, which are the contract a reloading client replays on;
- cancel answering 200 for a run that already ended, because that is the state a stale click hits.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from chimera.api.orchestration_api import register_orchestration_api
from chimera.config import Settings
from tests.test_hierarchy import _READ_TASK, FakeBackend


def _read_sse(text: str) -> list[tuple[str, dict[str, Any]]]:
    events: list[tuple[str, dict[str, Any]]] = []
    event = ""
    for line in text.splitlines():
        if line.startswith("event:"):
            event = line[len("event:") :].strip()
        elif line.startswith("data:"):
            events.append((event, json.loads(line[len("data:") :].strip())))
    return events


@pytest.fixture()
def app_and_backend(tmp_path: Path) -> tuple[TestClient, FakeBackend]:
    backend = FakeBackend()
    app = FastAPI()
    settings = Settings(CHIMERA_HOME=str(tmp_path / "home"))
    register_orchestration_api(
        app,
        Depends(lambda: None),
        tmp_path,
        settings,
        backend_factory=lambda: backend,
    )
    return TestClient(app), backend


def test_the_preview_shows_the_plan_and_starts_no_workers(
    app_and_backend: tuple[TestClient, FakeBackend],
) -> None:
    client, backend = app_and_backend

    body = client.post("/api/orchestration/preview", json={"task": _READ_TASK}).json()

    assert body["shape"] == "parallel_read"
    assert len(body["subtasks"]) == 2
    assert body["would_fall_back"] is False
    # The one call it is allowed to make is the decompose, and it says so rather than claiming
    # to be free. No worker ever ran.
    assert body["decompose_spent"] is True
    assert not any(call["system"].startswith("You are a focused sub-worker") for call in backend.calls)


def test_a_write_task_previews_as_a_fallback_without_spending_anything(
    app_and_backend: tuple[TestClient, FakeBackend],
) -> None:
    client, backend = app_and_backend

    body = client.post(
        "/api/orchestration/preview", json={"task": "Implement the retry and fix the test"}
    ).json()

    assert body["would_fall_back"] is True
    assert body["fell_back_reason"] == "shape"
    # This branch is fully deterministic — classification and the estimate are both arithmetic.
    assert body["decompose_spent"] is False
    assert backend.calls == []


def test_an_empty_task_is_refused_before_anything_is_built(
    app_and_backend: tuple[TestClient, FakeBackend],
) -> None:
    client, _ = app_and_backend
    assert client.post("/api/orchestration/preview", json={"task": "   "}).status_code == 400
    assert client.post("/api/orchestration/hierarchy", json={"task": ""}).status_code == 400


def test_the_stream_opens_with_the_run_id_and_closes_with_the_answer(
    app_and_backend: tuple[TestClient, FakeBackend],
) -> None:
    client, _ = app_and_backend

    response = client.post("/api/orchestration/hierarchy", json={"task": _READ_TASK})
    frames = _read_sse(response.text)
    kinds = [kind for kind, _ in frames]

    # The id arrives before any work, so a Stop control can target the run from the first moment.
    assert kinds[0] == "run"
    assert frames[0][1]["run_id"]
    assert kinds[-1] == "done"
    assert frames[-1][1]["answer"] == "Final synthesized answer."
    assert kinds.count("worker_started") == 2


def test_every_frame_is_numbered_and_the_numbers_only_go_up(
    app_and_backend: tuple[TestClient, FakeBackend],
) -> None:
    client, _ = app_and_backend

    frames = _read_sse(client.post("/api/orchestration/hierarchy", json={"task": _READ_TASK}).text)

    seqs = [payload["seq"] for _, payload in frames]
    # Strictly increasing with no gaps. A client that reconnects asks for everything after the
    # last number it saw; a repeated or missing one duplicates or loses a card.
    assert seqs == list(range(1, len(seqs) + 1))


def test_worker_frames_carry_a_task_id_the_decomposition_already_named(
    app_and_backend: tuple[TestClient, FakeBackend],
) -> None:
    client, _ = app_and_backend

    frames = _read_sse(client.post("/api/orchestration/hierarchy", json={"task": _READ_TASK}).text)
    by_kind: dict[str, list[dict[str, Any]]] = {}
    for kind, payload in frames:
        by_kind.setdefault(kind, []).append(payload)

    published = {spec["task_id"] for spec in by_kind["decomposed"][0]["specs"]}
    started = {payload["task_id"] for payload in by_kind["worker_started"]}
    assert published == started and len(published) == 2


def test_a_write_task_streams_the_fallback_as_a_fact_not_an_error(
    app_and_backend: tuple[TestClient, FakeBackend],
) -> None:
    client, _ = app_and_backend

    frames = _read_sse(
        client.post(
            "/api/orchestration/hierarchy", json={"task": "Refactor the parser and fix the test"}
        ).text
    )
    kinds = [kind for kind, _ in frames]

    # No error frame: falling back is the single-agent path working as designed, and the most
    # common outcome by far. A client that renders `error` here tells the user it broke.
    assert "error" not in kinds
    assert kinds == ["run", "classified", "fell_back", "done"]
    fell = next(payload for kind, payload in frames if kind == "fell_back")
    assert fell["reason"] == "shape"


def test_cancelling_a_run_that_already_ended_is_not_an_error(
    app_and_backend: tuple[TestClient, FakeBackend],
) -> None:
    client, _ = app_and_backend

    response = client.post("/api/orchestration/runs/does-not-exist/cancel")

    # 200, not 404: a Stop click that lands after the run finished is a normal thing to happen,
    # not a client mistake, and a UI should not have to special-case an error for it.
    assert response.status_code == 200
    assert response.json() == {"ok": False, "cancelled": False}


def test_the_ledger_reports_nothing_rather_than_zero_when_there_is_nothing(
    app_and_backend: tuple[TestClient, FakeBackend],
) -> None:
    client, _ = app_and_backend

    summary = client.get("/api/orchestration/delegations").json()["summary"]

    assert summary["n"] == 0
    # Null, never 0.0: "no receipts carry a price" and "the hierarchy saved nothing" are different
    # claims, and rendering the first as $0.00 invents a measurement.
    assert summary["usd_saving"] is None
    assert summary["token_saving"] is None


def test_the_ledger_adds_up_what_a_real_run_wrote(
    app_and_backend: tuple[TestClient, FakeBackend],
) -> None:
    client, _ = app_and_backend

    client.post("/api/orchestration/hierarchy", json={"task": _READ_TASK})
    summary = client.get("/api/orchestration/delegations").json()["summary"]

    assert summary["n"] > 0
    assert summary["measured_tokens"] > 0
    assert summary["by_tier"]


def test_the_frame_shapes_are_published_for_the_generated_client(
    app_and_backend: tuple[TestClient, FakeBackend],
) -> None:
    client, _ = app_and_backend

    shape = client.get("/api/orchestration/schema").json()

    # An SSE route cannot declare a response_model, so this is how the payload types reach the
    # schema at all. Empty and side-effect-free: a shape sample, never fabricated results.
    assert {
        "classified", "decomposed", "worker_started", "worker_verified", "worker_rejected",
        "fell_back", "done",
    } <= set(shape)
    assert shape["done"]["answer"] == ""
    assert shape["decomposed"]["specs"] == []

def test_the_plan_that_was_approved_is_the_plan_that_runs(
    app_and_backend: tuple[TestClient, FakeBackend],
) -> None:
    client, backend = app_and_backend

    plan = client.post("/api/orchestration/preview", json={"task": _READ_TASK}).json()
    assert plan["plan_id"], "a fan-out plan must be keepable, or approving it means nothing"
    decomposes_after_preview = sum(
        1 for call in backend.calls if "Split the user's task" in call["system"]
    )

    frames = _read_sse(
        client.post(
            "/api/orchestration/hierarchy",
            json={"task": _READ_TASK, "plan_id": plan["plan_id"]},
        ).text
    )

    published = [
        spec["objective"]
        for kind, payload in frames
        if kind == "decomposed"
        for spec in payload["specs"]
    ]
    # Same objectives, and NO second decompose call. Decomposition runs at a non-zero temperature,
    # so asking twice is how a preview promises one worker and the run delivers three.
    assert published == plan["subtasks"]
    total_decomposes = sum(
        1 for call in backend.calls if "Split the user's task" in call["system"]
    )
    assert total_decomposes == decomposes_after_preview


def test_an_expired_plan_decomposes_again_instead_of_failing(
    app_and_backend: tuple[TestClient, FakeBackend],
) -> None:
    client, _ = app_and_backend

    frames = _read_sse(
        client.post(
            "/api/orchestration/hierarchy",
            json={"task": _READ_TASK, "plan_id": "a-plan-this-process-never-had"},
        ).text
    )
    kinds = [kind for kind, _ in frames]

    # A restart loses the plan store. That must cost a model call, never an error: the run still
    # produces an answer, exactly as it did before plans were kept at all.
    assert "error" not in kinds
    assert kinds[-1] == "done"
    assert kinds.count("worker_started") == 2

def test_workers_can_open_files_but_never_change_them(
    app_and_backend: tuple[TestClient, FakeBackend],
) -> None:
    """The fix for a fan-out that described a file it had never opened.

    Asked about a 16-line module with two functions, the tool-free version produced a confident
    account of a class with cupons and stock control — and all three workers passed verification,
    because the verifier checks a summary against what the worker WROTE, never against the world.

    What must not come with the fix is write access: N workers in one folder with no worktree
    between them is the collision `IsolatedCrew` exists to prevent.
    """
    from chimera.api.orchestration_api import _WORKER_TOOLS

    assert "read_file" in _WORKER_TOOLS, "a worker that cannot read still has to answer"
    forbidden = {
        "write_file", "edit_file", "apply_patch",       # N workers, one folder, no worktree
        "run_shell", "execute_code", "code_interpreter",  # arbitrary effects
        "http_get", "browser", "crawl", "scrape",       # untrusted content, per-worker ledgers
    }
    assert not forbidden & set(_WORKER_TOOLS)


def test_a_worker_is_told_not_to_describe_what_it_has_not_opened(
    app_and_backend: tuple[TestClient, FakeBackend],
) -> None:
    from chimera.orchestration.hierarchy import WORKER_SYSTEM

    # A tool the model does not know it should reach for is a tool it will answer around.
    assert "READ IT" in WORKER_SYSTEM
    assert "Never describe a file you have not opened" in WORKER_SYSTEM


def test_max_usd_is_enforced_and_absent_means_no_ceiling(
    app_and_backend: tuple[TestClient, FakeBackend], tmp_path: Path
) -> None:
    """The other field this route accepted and never read.

    The unit tests next door prove `SpendCappedBackend` stops a run; this proves the ROUTE reaches
    for it, which is the half that was missing — the mechanism has existed since
    `AgentConfig.max_usd` and the whole defect was that nothing here called it.

    The fake answers as a model no price table knows, and `SpendBudget` refuses rather than
    guessing: an unpriced call means the spend so far is unknown, so the ceiling cannot say it is
    under. That is the documented rule, not an artefact of the fake — a ceiling that skips what it
    cannot price shows green while the bill climbs.
    """
    client, _backend = app_and_backend
    corpo = {"task": _READ_TASK, "workspace": str(tmp_path)}

    # A millionth of a dollar: the decompose alone costs more than that, so the ceiling is already
    # spent when the workers ask. A larger figure would pass for the wrong reason — the fake bills
    # 150 tokens a call on a real ladder slug, which is fractions of a cent, so a $0.01 cap never
    # fires and the assertion would be measuring the fake's appetite rather than the ceiling.
    capped = _read_sse(client.post("/api/orchestration/hierarchy", json={**corpo, "max_usd": 1e-6}).text)
    solto = _read_sse(client.post("/api/orchestration/hierarchy", json=corpo).text)

    motivos = {p.get("reason") for k, p in capped if k == "worker_rejected"}
    assert "spend" in motivos, (
        f"workers came back with {motivos or 'nothing'} — a dollar cap must not reach the screen "
        "as a delegation-token cut or a provider fault"
    )
    # The ending says which ceiling and how much of it went, rather than "the run failed" — this is
    # the one failure the caller asked for, and reporting a working cap as a fault sends them
    # looking for a bug.
    erro = next((p.get("message", "") for k, p in capped if k == "error"), "")
    assert "spend cap" in erro, f"the run ended saying {erro!r}"

    # The control: same task, same fake, no ceiling — and nothing capped. Without it, a wrapper
    # that refused every call would pass everything above.
    assert not [p for k, p in solto if k == "worker_rejected" and p.get("reason") == "spend"], (
        "a run with no ceiling was capped anyway"
    )
    assert [p for k, p in solto if k == "done" and p.get("total_tokens")], "the free run produced nothing"


# --- the crew: N roles, one task, one worktree each ------------------------------------------


def _crew(client: TestClient, **over: Any) -> list[tuple[str, dict[str, Any]]]:
    body = {
        "task": "corrija o bug do desconto",
        "workers": [
            {"name": "cauteloso", "instruction": "Faça a menor mudança possível."},
            {"name": "direto", "instruction": "Reescreva a função inteira se for mais claro."},
        ],
        **over,
    }
    return _read_sse(client.post("/api/orchestration/crew", json=body).text)


def test_synthesize_produces_a_report_and_off_produces_none(
    app_and_backend: tuple[TestClient, FakeBackend], tmp_path: Path
) -> None:
    """`synthesize` was accepted, documented, published to the TypeScript client — and read by
    nothing.

    Everything downstream already worked: `IsolatedCrew` emits the summary on its `done` frame, the
    reducer stores it, `CrewRun` renders it. The one missing link was the supervisor whose presence
    is what makes the crew synthesise at all, so the switch reached a route that never built one.

    Both directions in one test, deliberately: "on produces a report" passes just as well if the
    report was always there, and this field's whole meaning is that it costs a top-model call the
    caller did not have to pay.
    """
    client, _backend = app_and_backend

    ligado = _crew(client, workspace=str(tmp_path), synthesize=True)
    desligado = _crew(client, workspace=str(tmp_path), synthesize=False)

    def resposta(frames: list[tuple[str, dict[str, Any]]]) -> str:
        return str(next((p for k, p in frames if k == "crew_done"), {}).get("answer") or "")

    assert resposta(ligado), "synthesize=true still produced no report"
    assert not resposta(desligado), "a report was written for a caller who did not ask to pay for one"


def test_a_crew_reports_every_worker_by_name(
    app_and_backend: tuple[TestClient, FakeBackend], tmp_path: Path
) -> None:
    client, _ = app_and_backend

    frames = _crew(client, workspace=str(tmp_path))
    kinds = [kind for kind, _ in frames]

    assert kinds[0] == "run"
    started = {p["task_id"] for k, p in frames if k == "crew_worker_started"}
    # The role name is the routing key. Two cards, named for the two roles.
    assert started == {"cauteloso", "direto"}
    assert kinds[-1] in {"crew_done", "error"}


def test_each_worker_says_which_checkout_it_writes_in(
    app_and_backend: tuple[TestClient, FakeBackend], tmp_path: Path
) -> None:
    """In a git repository, one worktree each — and the frame names it.

    Which checkout produced which change used to be invisible: `run_isolated` creates them, uses
    them and removes them without ever saying their names, so a person watching parallel edits
    could not go look at one afterwards.
    """
    import subprocess

    repo = tmp_path / "repo"
    repo.mkdir()
    for args in (["init", "-q"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)
    (repo / "a.txt").write_text("x", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-qm", "x"], cwd=repo, check=True, capture_output=True)

    frames = _crew(client := app_and_backend[0], workspace=str(repo))
    started = [p for k, p in frames if k == "crew_worker_started"]
    done = next((p for k, p in frames if k == "crew_done"), {})

    assert all(p["workspace"] for p in started)
    assert len({p["workspace"] for p in started}) == len(started), "one checkout each, not shared"
    assert done.get("is_repo") is True
    del client


def test_outside_a_repository_it_says_the_workers_shared_one_folder(
    app_and_backend: tuple[TestClient, FakeBackend], tmp_path: Path
) -> None:
    """No git, no isolation — and the run has to admit it rather than imply worktrees.

    This is the case where "each worker in its own checkout" stops being true: they all edit the
    same directory, and a file two of them touch cannot even be detected as a conflict.
    """
    client, _ = app_and_backend

    frames = _crew(client, workspace=str(tmp_path))
    done = next((p for k, p in frames if k == "crew_done"), {})
    started = [p for k, p in frames if k == "crew_worker_started"]

    assert done.get("is_repo") is False
    # And the frames do not pretend otherwise: the shared folder is reported as what it is.
    assert len({p["workspace"] for p in started}) == 1


def test_two_workers_with_the_same_name_are_refused(
    app_and_backend: tuple[TestClient, FakeBackend],
) -> None:
    client, _ = app_and_backend

    response = client.post(
        "/api/orchestration/crew",
        json={
            "task": "x",
            "workers": [
                {"name": "revisor", "instruction": "a"},
                {"name": "revisor", "instruction": "b"},
            ],
        },
    )

    # Distinct names are not tidiness: the name IS the frame's routing key, so two of them would
    # collapse into one card reporting both workers' results as if they were one.
    assert response.status_code == 400


def test_a_crew_needs_workers(app_and_backend: tuple[TestClient, FakeBackend]) -> None:
    client, _ = app_and_backend
    assert client.post("/api/orchestration/crew", json={"task": "x", "workers": []}).status_code == 400


def test_the_crew_frame_shapes_are_published(
    app_and_backend: tuple[TestClient, FakeBackend],
) -> None:
    client, _ = app_and_backend

    shape = client.get("/api/orchestration/schema").json()

    for frame in ("crew_worker_started", "crew_worker_verified", "crew_worker_rejected",
                  "conflict", "crew_done"):
        assert frame in shape, f"{frame} must reach OpenAPI — an SSE route declares no response model"
    # The honesty flag the screen needs when the folder is not a repository.
    assert shape["crew_done"]["is_repo"] is False


def test_the_crew_governs_what_its_workers_may_touch(
    app_and_backend: tuple[TestClient, FakeBackend],
) -> None:
    from chimera.api.code_api import CodeSeams
    from chimera.api.orchestration_api import CrewRunIn

    # `HierarchyRunIn` declined to inherit CodeSeams and said in its docstring that CrewRunIn
    # would, "because those workers really do write files". This is that promise, as a test.
    assert issubclass(CrewRunIn, CodeSeams)
    assert "write_region" in CrewRunIn.model_fields


def test_a_folder_that_is_not_there_is_refused_by_name(
    app_and_backend: tuple[TestClient, FakeBackend],
) -> None:
    client, _ = app_and_backend

    response = client.post(
        "/api/orchestration/crew",
        json={
            "task": "implemente o desconto",
            "verify": "pytest -q",
            "workspace": r"C:\Users\alguem\pasta-que-nao-existe",
            "workers": [
                {"name": "conservador", "instruction": "a"},
                {"name": "direto", "instruction": "b"},
            ],
        },
    )

    # `Path.resolve()` validates nothing: a path this OS cannot parse becomes a plausible absolute
    # path glued onto the process directory. The crew then ran against a folder that was never
    # there and reported every worker as "your check failed" — pointing at code no one had read.
    assert response.status_code == 400
    assert "pasta-que-nao-existe" in response.json()["detail"]


def test_the_hierarchy_refuses_the_same_missing_folder(
    app_and_backend: tuple[TestClient, FakeBackend],
) -> None:
    client, _ = app_and_backend

    response = client.post(
        "/api/orchestration/hierarchy",
        json={"task": "compare os arquivos", "workspace": "/nao/existe/em/lugar/nenhum"},
    )

    # Same check on both doors. The hierarchy's workers only read, so a missing folder shows up as
    # workers who found nothing rather than as an error — quieter, and just as wrong.
    assert response.status_code == 400


def test_the_ready_made_approaches_are_served_rather_than_bundled(
    app_and_backend: tuple[TestClient, FakeBackend],
) -> None:
    client, _ = app_and_backend

    body = client.get("/api/orchestration/approaches").json()

    # The instruction is a model prompt, so it lives with the backend's other prompts and travels
    # to the app — not copied into the bundle, where changing one would mean a desktop release.
    assert len(body["approaches"]) >= 4
    assert all(item["id"] and item["instruction"] for item in body["approaches"])
    ids = {item["id"] for item in body["approaches"]}
    assert len(body["default"]) == 2 and set(body["default"]) <= ids
    # The pair a crew opens with must not be the same approach twice, which is exactly the
    # arrangement where both workers write the same diff and the conflict rule discards both.
    assert body["default"][0] != body["default"][1]


def test_a_worker_says_what_it_wrote_even_when_it_was_thrown_away(
    app_and_backend: tuple[TestClient, FakeBackend], tmp_path: Path
) -> None:
    """The account of a discarded attempt, which otherwise does not survive the run.

    The worktree is removed as the batch collects, so the file list has to be read there or not
    at all. Before this, a run where every worker was rejected reported that three attempts had
    happened and nothing whatsoever about what they were.
    """
    client, _ = app_and_backend

    # `false` never exits 0, so every worker is rejected and nothing merges.
    frames = _crew(client, workspace=str(tmp_path), verify="false")
    produced = {p["task_id"]: p for k, p in frames if k == "crew_worker_produced"}

    assert set(produced) == {"cauteloso", "direto"}, "reported for the discarded ones too"
    assert all(p["landed"] is False for p in produced.values())
    # And the frame is published, so the generated client has the shape.
    assert "crew_worker_produced" in client.get("/api/orchestration/schema").json()


def test_numbering_persisting_and_enqueuing_happen_under_one_lock() -> None:
    """The ordering invariant, asserted where it cannot be flaky.

    `test_every_frame_is_numbered_and_the_numbers_only_go_up` is the behavioural version, and it is
    timing-dependent: the same commit passed on 3.11 and 3.13 and failed on 3.12 with
    `[1, 2, 3, 5, 4, ...]`. A test that only sometimes sees the defect is a test the defect gets
    past, so the shape is pinned here as well.

    What went wrong is worth stating plainly, because "out of order" undersells it. The client's
    reducer drops a frame whose `seq` is not greater than the last it applied — that is what makes
    replay-after-reload idempotent — so a frame that arrives late is not reordered, it is GONE. Two
    workers stamping 4 and 5 and handing them over the other way round lose card 4 from the screen,
    with the run still reporting itself healthy.

    The window always existed and was one statement wide. Persisting the transcript put a file
    append inside it, which is what made it happen.
    """
    import ast
    import inspect

    from chimera.api import orchestration_api

    source = inspect.getsource(orchestration_api)
    emits = [
        node
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.FunctionDef) and node.name == "emit"
    ]
    assert len(emits) == 2, f"expected the hierarchy's emit and the crew's, found {len(emits)}"

    for emit in emits:
        withs = [n for n in emit.body if isinstance(n, ast.With)]
        assert len(withs) == 1, "the whole body's work belongs to one `with seq_lock:`"
        inside = ast.dump(ast.Module(body=withs[0].body, type_ignores=[]))
        assert "AugAssign" in inside and "numbered" in inside, (
            "the number is stamped inside the lock"
        )
        assert "runlog" in inside, "the transcript is written inside the lock"
        assert "call_soon_threadsafe" in inside, (
            "the frame is handed to the stream inside the lock — outside it, two workers can stamp "
            "4 and 5 and enqueue them the other way round, and the client DROPS 4"
        )
