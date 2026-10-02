"""A scheduled run is told what it is, posts only what is worth posting, and holds only its tools.

Study 28 (P3, R6, P10). Before this, an LLM cron job:

* reached the model with the coding prompt and ``Task: <action>`` and nothing about its situation —
  not that nobody was watching, not that its answer would be posted as it stood;
* had every answer posted (``make_deliver`` sent all of them), so a monitor that found the same
  thing every five minutes posted it every five minutes, and one with nothing to say had to say
  something anyway;
* could stop to ask questions nobody would read: the "assume" nudge existed only with
  ``insist_on_action``, which cron never set;
* ran with every tool in the registry, whatever the job needed.

Each test drives the real producer — ``make_run_job``, ``make_agent_dispatch``, ``make_deliver``,
``CronStore`` — with a fake backend and no network.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from chimera.config import Settings
from chimera.core.agent import _ACTION_NUDGE, _ASSUME_NUDGE, Agent, AgentConfig
from chimera.providers.gateway import CompletionResult, ToolCall
from chimera.scheduler import CronStore, Scheduler, make_agent_dispatch
from chimera.scheduler.delivery import Delivered, make_deliver, skip_reason
from chimera.scheduler.job_runner import make_run_job
from chimera.scheduler.models import CronJob, JobOutcome
from chimera.scheduler.results import load_results
from chimera.scheduler.surface import (
    NOTHING_NEW,
    answer_fingerprint,
    is_nothing_new,
    scheduled_run_note,
)
from chimera.tools.builtin import EchoTool
from chimera.tools.registry import ToolRegistry

WEBHOOK = "https://discord.com/api/webhooks/123/segredo-do-canal"


class _Scripted:
    """Answers from a list, and keeps every request it was sent."""

    def __init__(self, answers: list[CompletionResult]) -> None:
        self.answers = list(answers)
        self.calls: list[list[Any]] = []
        self.tools: list[list[str]] = []

    def complete(self, messages: list[Any], **kwargs: Any) -> CompletionResult:
        self.calls.append(list(messages))
        self.tools.append(_tool_names(kwargs.get("tools")))
        return self.answers.pop(0) if self.answers else CompletionResult(content="ok", model="fake")


def _tool_names(schemas: Any) -> list[str]:
    nomes: list[str] = []
    for schema in schemas or []:
        if isinstance(schema, dict):
            funcao = schema.get("function")
            nome = funcao.get("name") if isinstance(funcao, dict) else schema.get("name")
            if nome:
                nomes.append(str(nome))
    return nomes


def _content(message: Any) -> str:
    return str(message.get("content") if isinstance(message, dict) else message.content)


def _job(**overrides: Any) -> CronJob:
    campos: dict[str, Any] = {
        "id": "j1", "name": "portfolio watch", "trigger": "cron", "schedule": "* * * * *",
        "action": "check the portfolio and report what changed",
    }
    campos.update(overrides)
    return CronJob(**campos)


def _run_job(
    tmp_path: Path, backend: _Scripted, warned: list[str] | None = None, **settings_env: str
) -> Any:
    home = tmp_path / "home"
    (tmp_path / "proj").mkdir(exist_ok=True)
    settings = Settings(**{"CHIMERA_HOME": str(home), **settings_env})
    return make_run_job(
        settings=settings, backend=backend, workspace=tmp_path / "proj", model="fake",
        max_steps=4, usage_path=home / "usage.jsonl",
        warn=(warned.append if warned is not None else lambda _linha: None),
    )


class _Sink:
    """`send` for make_deliver: records what would have gone on the wire."""

    def __init__(self, ok: bool = True) -> None:
        self.ok = ok
        self.sent: list[str] = []

    def __call__(self, url: str, text: str) -> Delivered:
        self.sent.append(text)
        return Delivered(self.ok, "HTTP 204" if self.ok else "HTTP 500")


def _records(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


# --------------------------------------------------------------------------- old jobs.json


def test_a_jobs_file_written_before_these_fields_loads_unchanged(tmp_path: Path) -> None:
    """A crontab from the release before this one has none of `notify`, `tools` or the fingerprint.
    It has to load, keep every field it had, and behave exactly as it did: every answer posted, every
    tool offered."""
    path = tmp_path / "jobs.json"
    antigo = {
        "id": "a1b2c3d4", "name": "morning brief", "trigger": "cron", "schedule": "0 7 * * *",
        "action": "summarise the day", "created_by": "human", "enabled": True, "disabled_by": "",
        "next_run": 1.0, "last_run": None, "last_status": "ok", "last_error": None,
        "consecutive_failures": 0, "deliver_to": WEBHOOK, "max_usd": None, "critical": False,
        "workspace": None, "verify": "", "max_attempts": 1, "metadata": {},
    }
    path.write_text(json.dumps([antigo]), encoding="utf-8")

    store = CronStore(path)

    assert not store.stale, "an old file was read as a format from another version"
    job = store.get("a1b2c3d4")
    assert job.notify == "always" and job.tools is None and job.last_delivered_hash is None
    assert job.model_dump(include=set(antigo)) == antigo
    assert skip_reason(job, "the day was quiet") == "", "an old job stopped posting its answers"


# --------------------------------------------------------------------------- the note


def test_the_scheduled_run_is_told_it_is_unattended_and_where_its_answer_goes(tmp_path: Path) -> None:
    backend = _Scripted([CompletionResult(content="All positions within limits.", model="fake")])

    _run_job(tmp_path, backend)(_job(deliver_to=WEBHOOK))

    sistema, usuario = _content(backend.calls[0][0]), _content(backend.calls[0][-1])
    assert "unattended scheduled run" in usuario
    assert "a chat channel (discord.com)" in usuario
    assert NOTHING_NEW in usuario
    assert "Nobody can answer questions" in usuario
    # In the turn context, never the system prompt: the system message stays the same bytes on
    # every turn so a provider can cache it.
    assert "unattended scheduled run" not in sistema
    # The webhook URL is a credential: whoever has it can post to the channel. The prompt is sent
    # to a provider and written to the trace, so only the host may appear.
    assert "segredo-do-canal" not in usuario and "/api/webhooks" not in usuario


def test_the_note_says_where_the_answer_goes_for_each_kind_of_job() -> None:
    assert "result log" in scheduled_run_note(_job())
    assert "only if this run fails" in scheduled_run_note(
        _job(deliver_to=WEBHOOK, notify="failures_only")
    )
    assert "a chat channel (discord.com)" in scheduled_run_note(
        _job(deliver_to=WEBHOOK, notify="on_change")
    )


# --------------------------------------------------------------------------- assume, don't ask


def test_a_scheduled_run_that_asks_questions_is_told_to_assume(tmp_path: Path) -> None:
    backend = _Scripted([
        CompletionResult(content="Which portfolio do you mean?\nShould I include crypto?", model="f"),
        CompletionResult(content="Assumed the main portfolio, no crypto. Nothing moved.", model="f"),
    ])

    outcome = _run_job(tmp_path, backend)(_job())

    assert len(backend.calls) == 2, "the questions ended the run with nobody to read them"
    assert _content(backend.calls[1][-1]) == _ASSUME_NUDGE
    assert isinstance(outcome, JobOutcome) and outcome.answer.startswith("Assumed")


def test_a_scheduled_report_with_no_tool_call_is_not_pushed_back(tmp_path: Path) -> None:
    """The reason the cron path does not simply turn on `insist_on_action`: that nudge pushes back
    any answer that called no tool, and a report written from what the run already knows is the job
    done, not a plan described."""
    backend = _Scripted([CompletionResult(content="Quiet day: nothing crossed a limit.", model="f")])

    _run_job(tmp_path, backend)(_job())

    assert len(backend.calls) == 1


def _echo_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(EchoTool())
    return registry


def test_assume_on_questions_is_only_the_assume_half() -> None:
    """Narration is not its business, and neither is a run that already worked with its tools."""
    narrou = _Scripted([CompletionResult(content="You can run:\n```\nmake\n```", model="f")])
    Agent(narrou, _echo_registry(), AgentConfig(assume_on_questions=True)).run("build")
    assert len(narrou.calls) == 1
    assert _ACTION_NUDGE not in [_content(m) for m in narrou.calls[0]]

    trabalhou = _Scripted([
        CompletionResult(content="", model="f", tool_calls=[ToolCall(id="c1", name="echo", arguments={"text": "x"})]),
        CompletionResult(content="Done. Want me to also check the logs?", model="f"),
    ])
    Agent(trabalhou, _echo_registry(), AgentConfig(assume_on_questions=True)).run("build")
    assert len(trabalhou.calls) == 2


# --------------------------------------------------------------------------- the sentinel


def test_the_nothing_new_reply_is_recorded_and_not_posted(tmp_path: Path) -> None:
    results = tmp_path / "cron_results.jsonl"
    sink = _Sink()
    deliver = make_deliver(results, send=sink)

    deliver(_job(deliver_to=WEBHOOK), f"  `{NOTHING_NEW}`\n")

    assert sink.sent == [], "the job said it had nothing new, and that was posted to the channel"
    (registro,) = _records(results)
    assert registro["skipped"] == "the job reported nothing new"
    assert "delivered" not in registro
    assert load_results(results)[0].skipped == "the job reported nothing new"


def test_a_reply_that_only_starts_with_the_sentinel_is_still_posted(tmp_path: Path) -> None:
    sink = _Sink()
    make_deliver(tmp_path / "r.jsonl", send=sink)(
        _job(deliver_to=WEBHOOK), f"{NOTHING_NEW}, but the disk is at 91%"
    )

    assert len(sink.sent) == 1
    assert not is_nothing_new(f"{NOTHING_NEW}, but the disk is at 91%")


def test_a_failed_run_is_posted_even_when_its_answer_is_the_sentinel(tmp_path: Path) -> None:
    sink = _Sink()
    make_deliver(tmp_path / "r.jsonl", send=sink)(
        _job(deliver_to=WEBHOOK), NOTHING_NEW, status="rejected"
    )

    assert len(sink.sent) == 1


# --------------------------------------------------------------------------- on_change


def test_on_change_skips_a_repeat_and_delivers_a_change(tmp_path: Path) -> None:
    results = tmp_path / "r.jsonl"
    sink = _Sink()
    deliver = make_deliver(results, send=sink)
    job = _job(deliver_to=WEBHOOK, notify="on_change")

    deliver(job, "BTC 61,200. ETH 2,950.")
    deliver(job, "BTC 61,200.\n  ETH 2,950.  \n")  # whitespace only: the same message
    deliver(job, "BTC 58,900. ETH 2,950.")

    assert sink.sent == ["**portfolio watch**\nBTC 61,200. ETH 2,950.",
                         "**portfolio watch**\nBTC 58,900. ETH 2,950."]
    assert [r.get("skipped", "") for r in _records(results)] == [
        "", "notify=on_change, and the answer is the same as the last one delivered", "",
    ]


def test_on_change_tries_again_after_a_post_that_failed(tmp_path: Path) -> None:
    """A post that failed was not seen. The same answer next time is still news to the person it
    was for, so the fingerprint moves only on a delivery that worked."""
    job = _job(deliver_to=WEBHOOK, notify="on_change")
    fora = _Sink(ok=False)
    make_deliver(tmp_path / "r.jsonl", send=fora)(job, "disk at 91%")
    assert job.last_delivered_hash is None

    volta = _Sink()
    make_deliver(tmp_path / "r.jsonl", send=volta)(job, "disk at 91%")
    assert volta.sent == ["**portfolio watch**\ndisk at 91%"]


def test_on_change_never_holds_back_a_failure(tmp_path: Path) -> None:
    sink = _Sink()
    deliver = make_deliver(tmp_path / "r.jsonl", send=sink)
    job = _job(deliver_to=WEBHOOK, notify="on_change")

    deliver(job, "verify failed", status="rejected")
    deliver(job, "verify failed", status="rejected")

    assert len(sink.sent) == 2, "a job that broke the same way twice went quiet the second time"


def test_the_last_delivered_answer_survives_a_restart(tmp_path: Path) -> None:
    """Persisted with the job: the daemon restarts, and on_change must not re-post the state it
    already posted. Driven through the scheduler, the dispatch and the store, as the daemon runs."""
    jobs = tmp_path / "home" / "scheduler" / "jobs.json"
    sched = Scheduler(CronStore(jobs), jitter=False)
    job = sched.schedule_cron(
        "watch", "* * * * *", "check", now=0.0, deliver_to=WEBHOOK, notify="on_change"
    )
    sink = _Sink()
    deliver = make_deliver(tmp_path / "r.jsonl", send=sink)
    dispatch = make_agent_dispatch(lambda _task: "unused", deliver, run_job=lambda _j: "same state")

    sched.run_due(now=(job.next_run or 0) + 1, dispatch=dispatch)
    reiniciado = Scheduler(CronStore(jobs), jitter=False)
    assert reiniciado.store.get(job.id).last_delivered_hash == answer_fingerprint("same state")
    reiniciado.run_due(now=(reiniciado.store.get(job.id).next_run or 0) + 1, dispatch=dispatch)

    assert len(sink.sent) == 1, "a restart forgot what was already posted"


# --------------------------------------------------------------------------- failures_only


def test_failures_only_posts_a_failure_and_nothing_else(tmp_path: Path) -> None:
    sink = _Sink()
    deliver = make_deliver(tmp_path / "r.jsonl", send=sink)
    job = _job(deliver_to=WEBHOOK, notify="failures_only")

    deliver(job, "all fine")
    deliver(job, "the gate rejected it", status="rejected")

    assert sink.sent == ["**portfolio watch**\nthe gate rejected it"]


def test_failures_only_hears_about_a_run_that_raised(tmp_path: Path) -> None:
    """An exception never reached the sink at all, so without this "only failures" would have meant
    "only what a verify gate rejected". The engine still records the error: it is re-raised."""
    sched = Scheduler(CronStore(tmp_path / "jobs.json"), jitter=False)
    job = sched.schedule_cron(
        "watch", "* * * * *", "check", now=0.0, deliver_to=WEBHOOK, notify="failures_only"
    )
    sink = _Sink()

    def _quebra(_job: CronJob) -> str:
        raise RuntimeError("provider down")

    dispatch = make_agent_dispatch(
        lambda _t: "", make_deliver(tmp_path / "r.jsonl", send=sink), run_job=_quebra
    )
    (ran,) = sched.run_due(now=(job.next_run or 0) + 1, dispatch=dispatch)

    assert ran.last_status == "error" and ran.consecutive_failures == 1
    assert len(sink.sent) == 1 and "RuntimeError: provider down" in sink.sent[0]


def test_always_keeps_its_old_contract_for_a_run_that_raised(tmp_path: Path) -> None:
    sink = _Sink()

    def _quebra(_job: CronJob) -> str:
        raise RuntimeError("provider down")

    dispatch = make_agent_dispatch(
        lambda _t: "", make_deliver(tmp_path / "r.jsonl", send=sink), run_job=_quebra
    )
    with pytest.raises(RuntimeError):
        dispatch(_job(deliver_to=WEBHOOK))

    assert sink.sent == []


def test_a_sink_written_before_status_existed_is_called_the_old_way(tmp_path: Path) -> None:
    recebidos: list[tuple[str, str]] = []
    dispatch = make_agent_dispatch(
        lambda _t: "", lambda job, answer: recebidos.append((job.id, answer)),
        run_job=lambda _j: JobOutcome("rejected work", ok=False),
    )

    assert dispatch(_job()) == "rejected"
    assert recebidos == [("j1", "rejected work")]


# --------------------------------------------------------------------------- a killed run


def test_a_killed_run_is_recorded_cancelled_and_not_posted(tmp_path: Path) -> None:
    """`JobOutcome.cancelled` was set by the runner and dropped by the dispatch, so a killed run was
    recorded `ok` — zeroing the failure count — and its partial answer posted as finished."""
    sched = Scheduler(CronStore(tmp_path / "jobs.json"), jitter=False)
    job = sched.schedule_cron("watch", "* * * * *", "check", now=0.0, deliver_to=WEBHOOK)
    job.consecutive_failures = 2
    sink = _Sink()
    dispatch = make_agent_dispatch(
        lambda _t: "", make_deliver(tmp_path / "r.jsonl", send=sink),
        run_job=lambda _j: JobOutcome("half an answer", cancelled=True),
    )

    (ran,) = sched.run_due(now=(job.next_run or 0) + 1, dispatch=dispatch)

    assert ran.last_status == "cancelled"
    assert ran.consecutive_failures == 2
    assert sink.sent == []


# --------------------------------------------------------------------------- the tools


def test_the_job_s_tool_list_is_the_registry_the_model_sees(tmp_path: Path) -> None:
    backend = _Scripted([CompletionResult(content="ok", model="f")])

    _run_job(tmp_path, backend)(_job(tools=["read_file", "http_get"]))

    assert sorted(backend.tools[0]) == ["http_get", "read_file"]


def test_a_job_with_no_tool_list_keeps_every_tool(tmp_path: Path) -> None:
    backend = _Scripted([CompletionResult(content="ok", model="f")])

    _run_job(tmp_path, backend)(_job())

    assert {"read_file", "run_shell", "http_get", "write_file"} <= set(backend.tools[0])


def test_an_empty_tool_list_grants_nothing(tmp_path: Path) -> None:
    backend = _Scripted([CompletionResult(content="ok", model="f")])

    _run_job(tmp_path, backend)(_job(tools=[]))

    assert backend.tools[0] == []


def test_a_job_cannot_reach_past_the_deployment_s_allowlist(tmp_path: Path) -> None:
    """The deployment fenced the install; a job naming a tool outside the fence does not get it."""
    backend = _Scripted([CompletionResult(content="ok", model="f")])

    avisos: list[str] = []
    _run_job(tmp_path, backend, avisos, CHIMERA_TOOL_ALLOWLIST="read_file,grep")(
        _job(tools=["read_file", "run_shell"])
    )

    assert backend.tools[0] == ["read_file"]
    assert any("run_shell" in aviso and "allowlist" in aviso for aviso in avisos)


def test_a_tool_the_job_names_and_does_not_get_is_said_out_loud(tmp_path: Path) -> None:
    """A misspelt name would otherwise leave the job without the tool and nobody told."""
    backend = _Scripted([CompletionResult(content="ok", model="f")])
    avisos: list[str] = []

    _run_job(tmp_path, backend, avisos)(_job(tools=["read_file", "reed_file"]))

    assert backend.tools[0] == ["read_file"]
    assert avisos == ["cron 'portfolio watch': runs without reed_file (no such tool, or not allowed)"]


# --------------------------------------------------------------------------- the surfaces


def test_the_cli_writes_notify_and_tools_and_lists_them(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from typer.testing import CliRunner

    from chimera.cli.main import app

    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path / "home"))
    runner = CliRunner()
    added = runner.invoke(app, [
        "cron", "add", "watch", "*/5 * * * *", "check the portfolio",
        "--notify", "on_change", "--tools", "read_file, http_get",
    ])
    assert added.exit_code == 0, added.output

    (job,) = CronStore(tmp_path / "home" / "scheduler" / "jobs.json").list()
    assert job.notify == "on_change" and job.tools == ["read_file", "http_get"]
    listed = runner.invoke(app, ["cron", "list"])
    assert "notify=on_change" in listed.output and "tools=read_file,http_get" in listed.output

    refused = runner.invoke(app, ["cron", "add", "x", "* * * * *", "y", "--notify", "sometimes"])
    assert refused.exit_code == 1


def test_the_route_stores_notify_and_tools_and_reads_them_back(tmp_path: Path) -> None:
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from chimera.api import build_api_app
    from chimera.interface import ChatSession
    from tests.test_api import _FakeAgent

    settings = Settings(CHIMERA_HOME=str(tmp_path / "home"))
    client = TestClient(build_api_app(lambda: ChatSession(_FakeAgent()), settings=settings))

    created = client.post("/api/cron", json={
        "name": "watch", "schedule": "*/5 * * * *", "action": "check",
        "notify": "failures_only", "tools": ["read_file"],
    }).json()
    assert created["notify"] == "failures_only" and created["tools"] == ["read_file"]

    antigo = client.post(
        "/api/cron", json={"name": "brief", "schedule": "0 7 * * *", "action": "summarise"}
    ).json()
    assert antigo["notify"] == "always" and antigo["tools"] is None

    refused = client.post("/api/cron", json={
        "name": "x", "schedule": "* * * * *", "action": "y", "notify": "sometimes",
    })
    assert refused.status_code == 422


# --------------------------------------------------------------------------- review fixes


def test_on_change_does_not_take_a_failure_for_the_last_answer_delivered(tmp_path: Path) -> None:
    """A failure is posted as a failure. If its text became the fingerprint, the next SUCCESSFUL run
    saying the same words would be skipped as "the same as last time" — and the owner, who only ever
    saw those words under a failure, would never hear that the job worked."""
    sink = _Sink()
    deliver = make_deliver(tmp_path / "r.jsonl", send=sink)
    job = _job(deliver_to=WEBHOOK, notify="on_change")

    deliver(job, "deploy at 3f2a1c", status="rejected")
    assert job.last_delivered_hash is None
    deliver(job, "deploy at 3f2a1c")

    assert len(sink.sent) == 2, "the success was held back behind the failure that preceded it"


def test_a_job_with_nowhere_to_post_records_no_skip_reason(tmp_path: Path) -> None:
    """`skipped` means "held back from the destination on purpose". A job with no destination held
    nothing back, so its records must not claim a decision was taken."""
    results = tmp_path / "r.jsonl"
    deliver = make_deliver(results, send=_Sink())

    deliver(_job(), NOTHING_NEW)
    deliver(_job(notify="failures_only"), "all quiet")

    assert [r.get("skipped") for r in _records(results)] == [None, None]
    assert [r.skipped for r in load_results(results)] == ["", ""]


@pytest.fixture
def _fresh_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    from chimera.config import get_settings

    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path / "home"))
    get_settings.cache_clear()
    yield tmp_path / "home"
    get_settings.cache_clear()


def test_a_webhook_job_refuses_a_tool_list_it_could_not_enforce(
    tmp_path: Path, _fresh_settings: Path
) -> None:
    """A webhook job runs through the chat gateway, which applies neither the tool list nor notify.
    Stored, `cron list` would print `tools=read_file` over a run holding every tool — a fence that
    reads as enforced and is not. So the scheduler and the CLI refuse the combination."""
    from typer.testing import CliRunner

    from chimera.cli.main import app

    sched = Scheduler(CronStore(tmp_path / "jobs.json"))
    with pytest.raises(ValueError, match="webhook"):
        sched.schedule_webhook("on push", "gh-push", "summarise", tools=["read_file"])
    with pytest.raises(ValueError, match="webhook"):
        sched.schedule_webhook("on push", "gh-push", "summarise", tools=[])
    with pytest.raises(ValueError, match="webhook"):
        sched.schedule_webhook("on push", "gh-push", "summarise", notify="on_change")
    assert sched.store.list() == []

    runner = CliRunner()
    for extra in (["--tools", "read_file"], ["--notify", "failures_only"]):
        refused = runner.invoke(
            app, ["cron", "add", "on push", "gh-push", "summarise", "--webhook", *extra]
        )
        assert refused.exit_code == 1, refused.output
        # Refused with a message, not by a traceback: an uncaught ValueError also exits 1.
        assert isinstance(refused.exception, SystemExit), refused.exception
        assert "webhook" in refused.output
    jobs = _fresh_settings / "scheduler" / "jobs.json"
    assert not jobs.exists() or CronStore(jobs).list() == []

    plain = runner.invoke(app, ["cron", "add", "on push", "gh-push", "summarise", "--webhook"])
    assert plain.exit_code == 0, plain.output
    (job,) = CronStore(jobs).list()
    assert job.trigger == "webhook" and job.tools is None and job.notify == "always"


def test_the_webhook_server_will_not_run_a_hand_written_job_with_a_tool_list(
    _fresh_settings: Path,
) -> None:
    """jobs.json is a file an owner can edit. A webhook job carrying `tools` got there by hand, and
    the gateway would run it with every tool; not running it is the only honest outcome."""
    from chimera.cli.main import _cron_store, _webhook_handler

    store = _cron_store()
    store.add(CronJob(
        id="w1", name="fenced", trigger="webhook", schedule="gh-push", action="summarise",
        tools=["read_file"],
    ))
    store.add(CronJob(
        id="w2", name="open", trigger="webhook", schedule="gh-push", action="summarise",
    ))

    received: list[str] = []

    class _Gateway:
        def on_message(self, message: Any) -> str:
            received.append(message.text)
            return "ok"

    _webhook_handler(_Gateway())("gh-push", {})  # type: ignore[arg-type]  # duck-typed gateway

    assert received == ["summarise"], "the hand-written fenced job ran with the gateway's registry"
