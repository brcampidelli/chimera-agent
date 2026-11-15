"""A share link's guest sees the one conversation, never the owner's other private sources.

Audit item P0-20 (2026-11), from the adversarial reviews of P0-08: the guest's turn was built by the
owner's machinery and every source that machinery reads was a way out. Pinned here, through the
builders and routes the share link uses:

* no tool that reads the owner's other conversations, mailbox, calendar or skill bundles, and no shell
  (a shell reads any path on the machine: ``cat <home>/agent.json``);
* nothing reads outside the conversation's folder, whatever ``CHIMERA_APPROVAL_MODE`` says;
* no MCP server the owner configured by hand;
* the owner's ``agent.json`` and global ``AGENTS.md`` never reach the guest's system prompt, nor the
  prompt of a sub-agent the guest's turn starts; the project's own instructions still do;
* the owner's hooks (``~/.claude/settings.json``) do not run on a guest's turn;
* background jobs are seen by the conversation that started them, not by the folder they ran in, and the
  "jobs finished" note and the job-limit refusal never name another conversation's command;
* the browser keeps no sign-in for a guest, whatever the cookie retention is;
* learned lessons are not retrieved on a guest's turn;
* a profile fact whose secret sits on a second line is left out (the owner's own prompt, too).

Written to fail, on the code before this change, at the BEHAVIOURAL assertion and not at a missing
argument: nothing here passes a keyword the old code lacks, and a job is recorded by writing the record
the old code already defined.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from chimera.api.code_api import CodeSeams, assemble_registry
from chimera.config import Settings, get_settings
from chimera.core.agent import Agent as RealAgent
from chimera.core.agent import AgentResult
from chimera.core.context_budget import RunState
from chimera.core.jobs import Job, JobRegistry, jobs_for
from chimera.interface import ChatSession
from chimera.providers.gateway import LLMGateway
from chimera.sandbox import LocalSandbox
from chimera.tools.shell import RunShellTool

OWNER_CLIENT = "Cliente reservado do dono: ORION-741"
OWNER_GLOBAL = "Regra global do dono: PROTOCOLO-ALFA"
PROJECT_RULE = "Regra do projeto: use tabs"
PRIVATE_COMMAND = "python processar_ORION_741.py"


class _Agent:
    """Records how the turn builder constructed its agent (registry, config, cards)."""

    built: list[dict[str, Any]] = []
    #: When set, every run writes a file here: a turn that changed something is the one that is verified.
    touch: Path | None = None

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        registry = args[1] if len(args) > 1 else None
        config = kwargs.get("config") or (args[2] if len(args) > 2 else None)
        self.run_state = RunState()
        type(self).built.append({"registry": registry, "config": config, "cards": kwargs.get("cards")})

    def run(self, task: str, *, on_edit: Any = None, **kw: Any) -> AgentResult:
        if type(self).touch is not None:
            type(self).touch.write_text(f"{time.time()}", encoding="utf-8")
            if on_edit:
                on_edit(type(self).touch.name, "+edit")
        return AgentResult(
            answer="ok", steps=1, stopped_reason="final",
            transcript=[{"role": "user", "content": task}, {"role": "assistant", "content": "ok"}],
            model="test/model",
        )


@pytest.fixture(autouse=True)
def _fresh_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    # The default registry reads the process settings (jobs, mailbox), so they point at the test's home.
    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-config"))
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _settings(tmp_path: Path, **extra: Any) -> Settings:
    return Settings(CHIMERA_HOME=str(tmp_path / "home"), CHIMERA_MEMORY_BACKEND="json", **extra)


def _registry(tmp_path: Path, *, guest: bool, sink: Any = None, **extra: Any) -> Any:
    ws = tmp_path / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    registry, _ = assemble_registry(
        CodeSeams(), ws, _settings(tmp_path, **extra), LLMGateway(), steps=4,
        surface="api:turn", guest=guest, approval_sink=sink,
    )
    return registry


def _install_bundle(home: Path) -> None:
    from chimera.skills.bundles import bundles_root

    folder = bundles_root(home) / "internal-procedure"
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text("owner procedure: secret-handshake", encoding="utf-8")
    (folder / "bundle.json").write_text(
        json.dumps({
            "name": "internal-procedure", "description": "owner only", "status": "active",
            "switched_on_at": "2026-11-01T00:00:00Z",
        }),
        encoding="utf-8",
    )


# ------------------------------------------------------------------ tools


_OWNER_ACCOUNTS: dict[str, Any] = {
    "CHIMERA_IMAP_HOST": "imap.example.com", "CHIMERA_IMAP_USER": "me",
    "CHIMERA_IMAP_PASSWORD": "pw", "CHIMERA_CALENDAR_ICS_URL": "https://example.com/c.ics",
}


def test_the_owner_has_the_tools_and_the_guest_has_none_of_them(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for key, value in _OWNER_ACCOUNTS.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    _install_bundle(tmp_path / "home")
    owner = _registry(tmp_path, guest=False)
    guest = _registry(tmp_path, guest=True)
    # The precondition: each tool really is on the owner's turn, so the guest's absence proves something.
    for name in ("recall_history", "read_email", "calendar_events", "skill_view", "run_shell", "execute_code",
                 "download_media"):
        assert name in owner, f"{name} should be on the owner's turn"
        assert name not in guest, f"{name} reached a guest's turn"


def test_a_guest_turn_mounts_no_mcp_server_of_the_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from chimera.integrations import mcp_pool

    asked: list[str] = []

    def fake(settings: Any) -> Any:
        asked.append("connectors")
        return None

    monkeypatch.setattr(mcp_pool, "connectors", fake)
    _registry(tmp_path, guest=True)
    assert asked == [], "a guest's turn went to the owner's MCP pool"
    _registry(tmp_path, guest=False)
    assert asked == ["connectors"]


# ------------------------------------------------------------------ nothing outside the folder


@pytest.mark.parametrize("mode", ["allow", "ask"])
def test_a_guest_cannot_read_a_file_outside_the_folder_in_any_approval_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    private = home / "agent.json"
    private.write_text('{"instructions": "ORION-741 is the private client"}', encoding="utf-8")
    monkeypatch.setenv("CHIMERA_APPROVAL_MODE", mode)
    get_settings.cache_clear()

    # A bound screen is what makes the file tools ask instead of refuse at once.
    sink = lambda *a, **k: None  # noqa: E731
    owner = _registry(tmp_path, guest=False, sink=sink)
    guest = _registry(tmp_path, guest=True, sink=sink)
    if mode == "allow":
        assert "ORION-741" in owner.get("read_file").run(path=str(private)), (
            "precondition: under `allow` the owner's own read of an outside file is approved"
        )
    def outcome(tool: Any, **kwargs: Any) -> str:
        try:
            return str(tool.run(**kwargs))
        except Exception as exc:  # a refusal raised by the jail is a refusal
            return f"refused: {type(exc).__name__}"

    assert "ORION-741" not in outcome(guest.get("read_file"), path=str(private))
    assert "ORION-741" not in outcome(guest.get("grep"), pattern="ORION", path=str(home))


# ------------------------------------------------------------------ sub-agents


def test_a_sub_agent_of_a_guest_turn_inherits_the_guest_and_reads_no_global_instructions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from chimera.core.research import WebResearcher

    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    (home / "AGENTS.md").write_text(OWNER_GLOBAL, encoding="utf-8")
    monkeypatch.setenv("CHIMERA_RESEARCH_AGENT", "1")
    get_settings.cache_clear()
    guest = _registry(tmp_path, guest=True)
    owner = _registry(tmp_path, guest=False)

    seen: list[str] = []

    class _Backend:
        def complete(self, messages: list[Any], **kw: Any) -> Any:
            seen.append(json.dumps(messages, default=str))
            from chimera.providers.gateway import CompletionResult

            return CompletionResult(content="done", model="fake")

    def prompt_of(registry: Any) -> str:
        seen.clear()
        tool: Any = registry.get("research_web")
        while hasattr(tool, "inner"):
            tool = tool.inner
        researcher: WebResearcher = tool._researcher
        researcher.backend = _Backend()
        researcher._registry = registry  # any registry with a tool: the prompt is what is read
        researcher.research("qual cliente?")
        return " ".join(seen)

    assert "PROTOCOLO-ALFA" in prompt_of(owner), "precondition: the owner's research agent reads it"
    assert "PROTOCOLO-ALFA" not in prompt_of(guest)


# ------------------------------------------------------------------ hooks


def _user_hook(tmp_path: Path) -> None:
    config = tmp_path / "claude-config"
    config.mkdir(parents=True, exist_ok=True)
    (config / "settings.json").write_text(
        json.dumps({"hooks": {"UserPromptSubmit": [{"hooks": [
            {"type": "command", "command": "echo 'private owner instruction ORION-741'"}
        ]}]}}),
        encoding="utf-8",
    )


def test_the_owners_user_hooks_do_not_run_on_a_guest_turn(tmp_path: Path) -> None:
    _user_hook(tmp_path)
    owner = _registry(tmp_path, guest=False)
    guest = _registry(tmp_path, guest=True)
    assert getattr(owner, "lifecycle", None) is not None, "precondition: the owner's turn carries the hook"
    assert getattr(guest, "lifecycle", None) is None


# ------------------------------------------------------------------ the browser


def test_a_guests_browser_keeps_no_sign_in_whatever_the_retention_says(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from chimera.tools.defer import _unwrapped

    for retention in ("shared", "conversation"):
        monkeypatch.setenv("CHIMERA_BROWSER_COOKIE_RETENTION", retention)
        get_settings.cache_clear()
        owner = _unwrapped(_registry(tmp_path, guest=False).get("browser"))
        guest = _unwrapped(_registry(tmp_path, guest=True).get("browser"))
        assert owner.cookie_retention == retention
        assert guest.cookie_retention == "ephemeral"


# ------------------------------------------------------------------ jobs


def _finished_job(home: Path, ws: Path, session: str | None, command: str, job_id: str) -> str:
    """A job record the way the registry writes one; no process is started."""
    job = Job(
        id=job_id, command=command, cwd=str(ws), pid=0, started_at=time.time() - 5,
        log=str(home / "jobs" / f"{job_id}.log"), state="finished", exit_code=0,
        finished_at=time.time(), extra={"session_id": session} if session else {},
    )
    jobs_for(home)._write(job)
    return job_id


def _client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, home: Path | None = None) -> TestClient:
    import chimera.core
    from chimera.api import build_api_app
    from chimera.core.instructions import AgentIdentity
    from chimera.core.instructions import save as save_identity
    from chimera.evolution.wiring import build_memory_manager

    home = home or tmp_path / "home"
    monkeypatch.setenv("CHIMERA_HOME", str(home))
    get_settings.cache_clear()
    _Agent.built = []
    _Agent.touch = None
    monkeypatch.setattr(chimera.core, "Agent", _Agent, raising=True)
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    (ws / "AGENTS.md").write_text(PROJECT_RULE, encoding="utf-8")
    home.mkdir(parents=True, exist_ok=True)
    save_identity(home, AgentIdentity(name="Dono", instructions=OWNER_CLIENT))
    (home / "AGENTS.md").write_text(OWNER_GLOBAL, encoding="utf-8")
    settings = Settings(CHIMERA_HOME=str(home), CHIMERA_MEMORY_BACKEND="json")
    memory = build_memory_manager(settings)
    return TestClient(
        build_api_app(lambda: ChatSession(_Agent()), workspace=ws, settings=settings, memory=memory)
    )


def _owner_turn(client: TestClient, tmp_path: Path) -> str:
    body = {"message": "hello there", "workspace": str(tmp_path / "ws"), "stream": False}
    text = client.post("/api/code/turn", json=body).text
    found = re.search(r'"session_id": ?"([0-9a-f]+)"', text)
    assert found, text[:300]
    return found.group(1)


def _guest_turn(client: TestClient, sid: str, message: str = "what is going on") -> dict[str, Any]:
    token = client.post(f"/api/code/sessions/{sid}/share").json()["token"]
    spoke = client.post(
        "/guest/api/turn", json={"message": message, "name": "Ana"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert spoke.status_code == 200
    return _Agent.built[-1]


def test_a_guest_sees_only_the_jobs_of_its_own_conversation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(tmp_path, monkeypatch)
    sid = _owner_turn(client, tmp_path)
    home, ws = tmp_path / "home", tmp_path / "ws"
    mine = _finished_job(home, ws, sid, "mine", "aaaa00000001")
    owners = _finished_job(home, ws, "another-conversation", PRIVATE_COMMAND, "bbbb00000002")
    unstamped = _finished_job(home, ws, None, "from before stamps", "cccc00000003")

    built = _guest_turn(client, sid)
    status = built["registry"].get("job_status")
    listing = status.run()
    assert mine in listing
    assert owners not in listing and unstamped not in listing and "ORION_741" not in listing
    assert "no such job" in status.run(job_id=owners)
    assert "no such job" in status.run(job_id=unstamped)
    # The owner's turn still lists the whole folder.
    everything = _registry(tmp_path, guest=False).get("job_status").run()
    assert all(i in everything for i in (mine, owners, unstamped))


def test_the_finished_jobs_note_a_guest_turn_is_handed_names_no_other_conversations_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(tmp_path, monkeypatch)
    sid = _owner_turn(client, tmp_path)
    home, ws = tmp_path / "home", tmp_path / "ws"
    _finished_job(home, ws, sid, "mine", "aaaa00000001")
    _finished_job(home, ws, "another-conversation", PRIVATE_COMMAND, "bbbb00000002")

    built = _guest_turn(client, sid)
    notes = str(built["config"].turn_notes)
    assert "aaaa00000001" in notes, "the guest is told of its own conversation's job"
    assert "ORION_741" not in notes and "bbbb00000002" not in notes
    # What the guest was not told is still the owner's to hear.
    from chimera.core.jobs import finished_note

    assert "bbbb00000002" in finished_note(home, ws)


def test_a_shell_job_is_stamped_with_the_conversation_that_started_it(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    jobs = JobRegistry(tmp_path / "home")
    shell = RunShellTool(ws, LocalSandbox(), confirm=None, jobs=jobs)
    shell.job_session = "S9"
    import sys

    out = shell.run(command=f'"{sys.executable}" -c "pass"', background=True)
    job_id = re.search(r"job ([0-9a-f]{12}) started", out)
    assert job_id, out
    assert (jobs.get(job_id.group(1)) or Job("", "", "", 0, 0, "")).extra.get("session_id") == "S9"


def test_the_job_limit_refusal_names_no_command_of_another_conversation(tmp_path: Path) -> None:
    import sys

    ws = tmp_path / "ws"
    ws.mkdir()
    jobs = JobRegistry(tmp_path / "home")
    jobs.configure_from_settings(1, 0)
    sleeper = f'"{sys.executable}" -c "import time; time.sleep(30)  # {PRIVATE_COMMAND}"'
    owners = RunShellTool(ws, LocalSandbox(), confirm=None, jobs=jobs)
    owners.job_session = "owner-conversation"
    started = owners.run(command=sleeper, background=True)
    try:
        assert "started in the background" in started, started
        owners_job = re.search(r"job ([0-9a-f]{12}) started", started)
        assert owners_job, started
        guests = RunShellTool(ws, LocalSandbox(), confirm=None, jobs=jobs)
        guests.job_session = "shared-conversation"
        guests.only_session = "shared-conversation"
        refusal = guests.run(command="python -c pass", background=True)
        assert "already running" in refusal
        assert "ORION_741" not in refusal and owners_job.group(1) not in refusal
    finally:
        for job in jobs.all():
            jobs.cancel(job.id)


# ------------------------------------------------------------------ the turn through the share link


def _prompt_of(built: dict[str, Any]) -> str:
    return RealAgent(object(), built["registry"], built["config"]).compose_system_prompt("hello")  # type: ignore[arg-type]


def test_the_owners_private_instructions_stay_out_of_the_guests_prompt_but_the_projects_stay_in(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(tmp_path, monkeypatch)
    sid = _owner_turn(client, tmp_path)
    owner_built = _Agent.built[-1]
    owner_prompt = _prompt_of(owner_built)
    assert "ORION-741" in owner_prompt and "PROTOCOLO-ALFA" in owner_prompt, (
        "precondition: the owner's prompt carries both"
    )
    assert PROJECT_RULE in owner_prompt

    built = _guest_turn(client, sid, "what is the client")
    assert built is not owner_built
    prompt = _prompt_of(built)
    assert "ORION-741" not in prompt and "PROTOCOLO-ALFA" not in prompt
    assert PROJECT_RULE in prompt, "the project's own instructions are the guest's to read"


def test_learned_lessons_are_retrieved_for_the_owner_and_not_for_a_guest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from chimera.api import code_api

    sentinel = object()
    monkeypatch.setattr(code_api, "_card_retriever", lambda settings, gateway: sentinel)
    client = _client(tmp_path, monkeypatch)
    sid = _owner_turn(client, tmp_path)
    assert _Agent.built[-1]["cards"] is sentinel, "precondition: the owner's turn gets the retriever"
    assert _guest_turn(client, sid, "repeat the private task")["cards"] is None


def test_a_guest_agent_does_not_card_retrieve_nor_list_the_owners_bundles(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(tmp_path, monkeypatch)
    _install_bundle(tmp_path / "home")
    sid = _owner_turn(client, tmp_path)
    owner_built = _Agent.built[-1]
    guest_built = _guest_turn(client, sid)

    class _Cards:
        def card_context(self, task: str) -> str:
            return "LESSON-FROM-A-PRIVATE-TURN"

    def context_of(built: dict[str, Any]) -> str:
        registry = built["registry"]
        registry.bundle_home = tmp_path / "home"
        agent = RealAgent(object(), registry, built["config"], cards=_Cards())  # type: ignore[arg-type]
        return agent.compose_turn_context("hello")

    owner_context = context_of(owner_built)
    assert "internal-procedure" in owner_context and "LESSON-FROM-A-PRIVATE-TURN" in owner_context
    guest_context = context_of(guest_built)
    assert "internal-procedure" not in guest_context
    assert "LESSON-FROM-A-PRIVATE-TURN" not in guest_context


# ------------------------------------------------------------------ the profile's redactor


def test_a_credential_on_the_second_line_of_a_profile_fact_is_left_out_not_masked() -> None:
    from chimera.interface.profile import _quote

    assert _quote("Contexto\nAuthorization: Basic dXNlcjpzZW5oYQ==") is None
    assert _quote("Contexto simples") is not None


# ------------------------------------------------------------------ nothing runs that the guest could have edited


def _stream_event(text: str, name: str) -> dict[str, Any]:
    event, found = "", {}
    for line in text.splitlines():
        if line.startswith("event: "):
            event = line[len("event: "):]
        elif line.startswith("data: ") and event == name:
            found = json.loads(line[len("data: "):])
    return found


def test_a_guest_turn_does_not_run_the_projects_approved_verifier(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import hashlib

    from chimera.core.code_projects import CodeProjectRegistry

    monkeypatch.setenv("CHIMERA_HOST_EXEC", "allow")
    get_settings.cache_clear()
    client = _client(tmp_path, monkeypatch)
    ws = tmp_path / "ws"
    (ws / ".chimera").mkdir()
    command = "echo ran > sentinel"
    (ws / ".chimera" / "verify.toml").write_text(f"command = {json.dumps(command)}\n", encoding="utf-8")
    projects = CodeProjectRegistry(tmp_path / "home" / "code_projects.json")
    projects.set_grant(str(ws), True)
    assert projects.approve_verify_hash(ws, hashlib.sha256(command.encode()).hexdigest())
    sid = _owner_turn(client, tmp_path)
    token = client.post(f"/api/code/sessions/{sid}/share").json()["token"]
    _Agent.touch = ws / "touched.txt"
    # The owner's own turn: the approved verifier runs, so the guest's refusal below proves something.
    (ws / "sentinel").unlink(missing_ok=True)
    owner_turn = client.post(
        "/api/code/turn",
        json={"message": "fix it", "workspace": str(ws), "session_id": sid},
    )
    assert _stream_event(owner_turn.text, "verified").get("state") == "passed"
    assert (ws / "sentinel").is_file(), "precondition: the owner's turn runs the approved verifier"
    (ws / "sentinel").unlink(missing_ok=True)

    spoke = client.post(
        "/guest/api/turn", json={"message": "fix it", "name": "Ana"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert spoke.status_code == 200
    verified = _stream_event(spoke.text, "verified")
    assert not (ws / "sentinel").exists(), "the guest's turn ran the project's verifier"
    assert verified.get("state") == "abstained"
    assert "guest" in str(verified.get("output", ""))


def test_a_guests_write_runs_no_formatter(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from chimera.tools import formatting

    calls: list[str] = []

    def fake(path: Path, workspace: Path) -> str:
        calls.append(path.name)
        return "FORMATTED-BY-THE-PROJECT-CONFIG"

    monkeypatch.setattr(formatting, "observe_format", fake)
    owner = _registry(tmp_path, guest=False)
    guest = _registry(tmp_path, guest=True)
    assert "FORMATTED" in owner.get("write_file").run(path="owner.js", content="x=1\n"), (
        "precondition: the owner's write is formatted"
    )
    out = guest.get("write_file").run(path="guest.js", content="x=1\n")
    assert "FORMATTED" not in out
    assert "guest.js" not in calls


def test_no_hook_runs_on_a_guest_turn_not_even_the_projects_own(tmp_path: Path) -> None:
    from chimera.governance.claude_hooks import trust_repo

    ws = tmp_path / "ws"
    (ws / ".claude").mkdir(parents=True)
    (ws / ".claude" / "settings.json").write_text(
        json.dumps({"hooks": {"UserPromptSubmit": [{"hooks": [
            {"type": "command", "command": "python scripts/hook.py"}
        ]}]}}),
        encoding="utf-8",
    )
    (tmp_path / "home").mkdir(parents=True, exist_ok=True)
    trust_repo(tmp_path / "home", ws)
    owner = _registry(tmp_path, guest=False)
    guest = _registry(tmp_path, guest=True)
    assert getattr(owner, "lifecycle", None) is not None, "precondition: the trusted project hook is the owner's"
    assert getattr(guest, "lifecycle", None) is None


def test_a_guests_dev_server_and_pull_request_tools_are_not_mounted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CHIMERA_PULL_REQUESTS", "1")
    get_settings.cache_clear()
    owner = _registry(tmp_path, guest=False)
    guest = _registry(tmp_path, guest=True)
    assert "open_pull_request" in owner
    assert "open_pull_request" not in guest


def test_an_agents_md_symlink_out_of_the_project_is_not_followed_for_a_guest_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(tmp_path, monkeypatch)
    ws = tmp_path / "ws"
    (ws / "AGENTS.md").unlink()
    try:
        (ws / "AGENTS.md").symlink_to(tmp_path / "home" / "AGENTS.md")
    except (OSError, NotImplementedError):
        pytest.skip("this machine cannot create symlinks")
    sid = _owner_turn(client, tmp_path)
    owner_prompt = _prompt_of(_Agent.built[-1])
    assert "PROTOCOLO-ALFA" in owner_prompt, "precondition: the owner's turn follows the link as it always did"
    guest_prompt = _prompt_of(_guest_turn(client, sid))
    assert "PROTOCOLO-ALFA" not in guest_prompt


def _repo_with_fsmonitor(ws: Path, marker: Path) -> None:
    """A repository whose configuration names a program for git to run on every `git status`."""
    import subprocess
    import sys

    script = ws.parent / "fsmonitor.py"
    script.write_text(f"open({str(marker)!r}, 'w').write('ran')\n", encoding="utf-8")

    def git(*args: str) -> None:
        subprocess.run(
            ["git", "-C", str(ws), "-c", "user.email=a@b.c", "-c", "user.name=t", *args],
            check=True, capture_output=True,
        )

    git("init", "-q")
    (ws / "a.txt").write_text("x", encoding="utf-8")
    git("add", "a.txt")
    git("commit", "-q", "-m", "first")
    git("config", "core.fsmonitor", f'"{sys.executable}" "{script}"')


def test_git_runs_nothing_the_repository_configures_on_a_guest_turn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import shutil

    if shutil.which("git") is None:
        pytest.skip("git is not installed")
    client = _client(tmp_path, monkeypatch)
    ws, marker = tmp_path / "ws", tmp_path / "fsmonitor-ran"
    _repo_with_fsmonitor(ws, marker)
    sid = _owner_turn(client, tmp_path)
    _Agent.touch = ws / "touched.txt"

    client.post("/api/code/turn", json={"message": "edit", "workspace": str(ws), "session_id": sid})
    assert marker.is_file(), "precondition: the owner's turn runs the repository's fsmonitor"
    owner_built = _Agent.built[-1]
    marker.unlink()

    guest_built = _guest_turn(client, sid, "edit")
    assert not marker.exists(), "the guest's turn ran the repository's fsmonitor (snapshot)"

    def context_of(built: dict[str, Any]) -> str:
        return RealAgent(object(), built["registry"], built["config"]).compose_turn_context("x")  # type: ignore[arg-type]

    context_of(owner_built)
    assert marker.is_file(), "precondition: the owner's turn context asks git for its status"
    marker.unlink()
    assert "git:" not in context_of(guest_built)
    assert not marker.exists(), "the guest's turn context ran the repository's fsmonitor"


@pytest.mark.parametrize("who", ["owner", "guest"])
def test_a_static_chart_fetches_no_data_url_for_anybody(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, who: str
) -> None:
    import sys
    import types

    drawn: list[dict[str, Any]] = []
    fake = types.ModuleType("vl_convert")
    fake.vegalite_to_svg = lambda spec, *a, **k: drawn.append(spec) or "<svg/>"  # type: ignore[attr-defined]
    fake.vegalite_to_png = lambda spec, *a, **k: drawn.append(spec) or b"png"  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "vl_convert", fake)
    tool = _registry(tmp_path, guest=who == "guest").get("render_chart")

    inline = {"data": {"values": [{"a": 1}]}, "mark": "point", "encoding": {"x": {"field": "a", "type": "quantitative"}}}
    assert "error" not in str(tool.run(spec=inline, format="svg", out="ok.svg")), "precondition: inline rows draw"
    assert len(drawn) == 1
    leak = {
        "data": {"url": "http://127.0.0.1:8765/api/instructions", "format": {"type": "json"}},
        "mark": "text", "encoding": {"text": {"field": "instructions", "type": "nominal"}},
    }
    for fmt in ("svg", "png"):
        out = str(tool.run(spec=leak, format=fmt, out=f"leak.{fmt}"))
        assert out.startswith("error"), out
    assert len(drawn) == 1, "the renderer was handed a spec that makes it fetch a url"
    assert not (tmp_path / "ws" / "leak.svg").exists()


def test_a_guests_browser_has_no_local_port_or_site_the_owner_declared(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    from chimera.tools.defer import _unwrapped

    class _Dev(BaseHTTPRequestHandler):
        def do_OPTIONS(self) -> None:
            self.send_response(204)
            self.end_headers()

        def log_message(self, *a: Any) -> None:
            pass

    server = HTTPServer(("127.0.0.1", 0), _Dev)  # something must answer on the declared port
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        port = server.server_address[1]
        monkeypatch.setenv("CHIMERA_BROWSER_LOCAL_PORTS", str(port))
        get_settings.cache_clear()
        owner = _unwrapped(_registry(tmp_path, guest=False).get("browser"))
        guest = _unwrapped(_registry(tmp_path, guest=True).get("browser"))
        local = f"http://127.0.0.1:{port}/api/instructions"
        owner._check_target(local)  # the precondition: the owner declared this port, so it opens
        with pytest.raises(ValueError):
            guest._check_target(local)
    finally:
        server.shutdown()


def _symlink(link: Path, target: Path) -> None:
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("this machine cannot create symlinks")


def test_a_snapshot_does_not_follow_a_symlink_out_of_the_workspace(tmp_path: Path) -> None:
    from chimera.core.checkpoint import WorkspaceGuard

    ws = tmp_path / "ws"
    ws.mkdir()
    outside = tmp_path / "agent.json"
    outside.write_text('{"instructions": "ORION-741"}', encoding="utf-8")
    _symlink(ws / "private.json", outside)
    (ws / "real.txt").write_text("v1", encoding="utf-8")
    _symlink(ws / "link.txt", ws / "real.txt")

    guard = WorkspaceGuard(ws)
    snap = guard.snapshot()
    assert "real.txt" in snap.present and "link.txt" in snap.present, "a link that stays inside is kept"
    assert "private.json" not in snap.present
    assert all("ORION-741" not in str(snap.files.get(rel)) for rel in snap.present)

    # An undo still puts back what was written through an inside link.
    (ws / "link.txt").write_text("v2", encoding="utf-8")
    guard.restore(snap)
    assert (ws / "real.txt").read_text(encoding="utf-8") == "v1"
    assert outside.read_text(encoding="utf-8") == '{"instructions": "ORION-741"}'


# ------------------------------------------------------------------ reading through a path that ends up outside


def _directory_link(link: Path, target: Path) -> None:
    """A junction on Windows (which Python 3.12's os.walk does not report as a link), else a symlink."""
    import sys

    if sys.platform == "win32":
        import _winapi

        _winapi.CreateJunction(str(target), str(link))
        return
    _symlink(link, target)


def test_the_verifier_is_not_inferred_from_a_file_outside_the_workspace(tmp_path: Path) -> None:
    from chimera.core.verify_infer import resolve_verify

    ws = tmp_path / "ws"
    ws.mkdir()
    outside = tmp_path / "agent.json"
    outside.write_text('{"scripts": {"test": "echo ORION-741"}}', encoding="utf-8")
    (ws / "package.json").write_text('{"scripts": {"test": "jest"}}', encoding="utf-8")
    assert resolve_verify(None, ws) == ("npm test", "inferred:package.json"), "precondition: an inside file infers"

    (ws / "package.json").unlink()
    _symlink(ws / "package.json", outside)
    assert resolve_verify(None, ws) == (None, "none")

    # A link in a DIRECTORY (a junction on Windows) is judged by where it really leads.
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "test_private.py").write_text("def test_x(): pass", encoding="utf-8")
    _directory_link(ws / "tests", elsewhere)
    assert resolve_verify(None, ws) == (None, "none")


def test_a_snapshot_does_not_descend_into_a_directory_link_or_junction(tmp_path: Path) -> None:
    from chimera.core.checkpoint import WorkspaceGuard

    ws = tmp_path / "ws"
    ws.mkdir()
    outside = tmp_path / "private"
    outside.mkdir()
    (outside / "agent.json").write_text('{"instructions": "ORION-741"}', encoding="utf-8")
    (ws / "inner").mkdir()
    (ws / "inner" / "ok.txt").write_text("kept", encoding="utf-8")
    _directory_link(ws / "private", outside)

    snap = WorkspaceGuard(ws).snapshot()
    assert "inner/ok.txt" in snap.present, "precondition: the workspace's own files are in the snapshot"
    assert not any(rel.startswith("private/") for rel in snap.present)
    assert all("ORION-741" not in str(snap.files.get(rel)) for rel in snap.present)


def test_a_playlist_or_manifest_is_not_transcribed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from chimera.tools import media

    ws = tmp_path / "ws"
    ws.mkdir()
    heard: list[str] = []
    monkeypatch.setattr(
        media, "_transcribe_faster_whisper", lambda path, language: heard.append(Path(path).name) or "transcript"
    )
    (ws / "real.wav").write_bytes(b"RIFF....WAVEfmt ")
    manifest = "#EXTM3U\n#EXTINF:10,\nfile:///srv/private/meeting.mp3\n"
    (ws / "leak.m3u8").write_text(manifest, encoding="utf-8")
    (ws / "renamed.mp3").write_text(manifest, encoding="utf-8")
    (ws / "list.dat").write_text("ffconcat version 1.0\nfile '/srv/private/meeting.mp3'\n", encoding="utf-8")
    tool = media.TranscribeAudioTool(ws)

    assert tool.run(path="real.wav") == "transcript", "precondition: audio is transcribed"
    for name in ("leak.m3u8", "renamed.mp3", "list.dat"):
        assert str(tool.run(path=name)).startswith("error"), name
    assert heard == ["real.wav"], "the decoder was handed a manifest"


def test_a_guest_has_no_transcription_tool(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHIMERA_OPENAI_KEYS", "sk-test")
    get_settings.cache_clear()
    assert "transcribe_audio" in _registry(tmp_path, guest=False), "precondition: the owner has it"
    assert "transcribe_audio" not in _registry(tmp_path, guest=True)


# ------------------------------------------------------------------ the data folder inside the workspace


def _registry_with_home_in_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, guest: bool) -> Any:
    ws = tmp_path / "ws"
    home = ws / ".chimera"
    home.mkdir(parents=True, exist_ok=True)
    (home / "agent.json").write_text('{"instructions": "ORION-741 is the private client"}', encoding="utf-8")
    (home / "notes.txt").write_text("history ORION-741", encoding="utf-8")
    (ws / "readme.txt").write_text("public", encoding="utf-8")
    monkeypatch.setenv("CHIMERA_HOME", str(home))
    get_settings.cache_clear()
    registry, _ = assemble_registry(
        CodeSeams(), ws, Settings(CHIMERA_HOME=str(home), CHIMERA_MEMORY_BACKEND="json"), LLMGateway(),
        steps=4, surface="api:turn", guest=guest, approval_sink=lambda *a, **k: None,
    )
    return registry


def _outcome(tool: Any, **kwargs: Any) -> str:
    try:
        return str(tool.run(**kwargs))
    except Exception as exc:  # the jail's refusal is a raised error
        return f"refused: {type(exc).__name__}"


def test_a_guest_cannot_read_list_or_search_the_data_folder_when_it_is_inside_the_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = _registry_with_home_in_workspace(tmp_path, monkeypatch, guest=False)
    assert "ORION-741" in _outcome(owner.get("read_file"), path=".chimera/agent.json"), "precondition"
    assert ".chimera" in _outcome(owner.get("list_dir"), path=".")
    assert "ORION-741" in _outcome(owner.get("grep"), pattern="ORION", path=".")

    guest = _registry_with_home_in_workspace(tmp_path, monkeypatch, guest=True)
    assert "ORION-741" not in _outcome(guest.get("read_file"), path=".chimera/agent.json")
    assert "ORION-741" not in _outcome(guest.get("read_file"), path=".chimera/../.chimera/notes.txt")
    assert "ORION-741" not in _outcome(guest.get("grep"), pattern="ORION", path=".")
    assert "ORION-741" not in _outcome(guest.get("grep"), pattern="ORION", path=".chimera")
    assert "agent.json" not in _outcome(guest.get("list_dir"), path=".chimera")
    assert ".chimera" not in _outcome(guest.get("list_dir"), path=".")
    assert ".chimera" not in _outcome(guest.get("glob"), pattern="**/*.json")
    assert "readme.txt" in _outcome(guest.get("list_dir"), path="."), "the project itself stays readable"


# ------------------------------------------------------------------ comparing real paths


def test_containment_is_exact_per_component_so_a_case_sensitive_sibling_is_not_the_workspace() -> None:
    """Simulated paths: a case-sensitive directory cannot be made on this machine without privilege.

    The components are what `os.path.realpath` returns; on a case-sensitive Windows directory `repo` and
    `REPO` are two folders, and `normcase` would have called the second one the workspace.
    """
    from chimera.core.contained import inside_parts

    root = ("C:\\", "areas", "repo")
    assert inside_parts(root, ("C:\\", "areas", "repo", "src", "a.py"))
    assert inside_parts(root, ("C:\\", "areas", "repo"))
    assert not inside_parts(root, ("C:\\", "areas", "REPO", "private", "a.py"))
    assert not inside_parts(root, ("C:\\", "areas", "repo2", "a.py"))
    assert not inside_parts(root, ("C:\\", "areas"))
    assert inside_parts(root, ("c:\\", "areas", "repo", "a.py")), "the drive letter alone is case-insensitive"
    assert not inside_parts((), ("C:\\",))


def test_a_workspace_named_in_another_case_still_contains_its_own_files(tmp_path: Path) -> None:
    import sys

    from chimera.core.contained import contained_path

    ws = tmp_path / "Repo"
    ws.mkdir()
    (ws / "a.txt").write_text("x", encoding="utf-8")
    assert contained_path(ws, ws / "a.txt") is not None
    outside = tmp_path / "other.txt"
    outside.write_text("x", encoding="utf-8")
    assert contained_path(ws, outside) is None
    if sys.platform != "win32":
        pytest.skip("a case-insensitive volume is how Windows (and macOS) behave; Linux is case-sensitive")
    # The workspace spelled in another case resolves to the same real folder, so its files are inside.
    assert contained_path(str(ws).upper(), ws / "a.txt") is not None
    assert contained_path(ws, str(ws / "a.txt").lower()) is not None


def test_an_at_import_of_a_private_file_is_not_followed_on_a_guest_turn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = tmp_path / "ws"
    home = ws / ".chimera"
    client = _client(tmp_path, monkeypatch, home=home)
    (home / "secret-notes.md").write_text("IMPORTED-SECRET-55", encoding="utf-8")
    (ws / "CLAUDE.md").write_text("@.chimera/secret-notes.md", encoding="utf-8")
    sid = _owner_turn(client, tmp_path)
    assert "IMPORTED-SECRET-55" in _prompt_of(_Agent.built[-1]), "precondition: the owner's turn follows the import"
    assert "IMPORTED-SECRET-55" not in _prompt_of(_guest_turn(client, sid))


def test_a_guest_can_neither_overwrite_nor_diff_a_private_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = tmp_path / "ws"
    (ws / ".claude").mkdir(parents=True)
    claude = ws / ".claude" / "CLAUDE.md"
    claude.write_text("owner rule ORION-741", encoding="utf-8")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(ws / ".claude"))
    owner = _registry_with_home_in_workspace(tmp_path, monkeypatch, guest=False)
    assert "error" not in _outcome(owner.get("edit_file"), path=".claude/CLAUDE.md", old="owner rule", new="owner RULE"), (
        "precondition: the owner may edit the file"
    )
    claude.write_text("owner rule ORION-741", encoding="utf-8")
    guest = _registry_with_home_in_workspace(tmp_path, monkeypatch, guest=True)
    notes = ws / ".chimera" / "notes.txt"
    for outcome in (
        _outcome(guest.get("write_file"), path=".claude/CLAUDE.md", content="replaced"),
        _outcome(guest.get("edit_file"), path=".claude/CLAUDE.md", old="ORION-741", new="x"),
        _outcome(guest.get("edit_file"), path=".chimera/notes.txt", old="history", new="x"),
        _outcome(guest.get("write_file"), path=".chimera/notes.txt", content="replaced"),
    ):
        assert "ORION-741" not in outcome
    assert claude.read_text(encoding="utf-8") == "owner rule ORION-741"
    assert notes.read_text(encoding="utf-8") == "history ORION-741"


def test_the_explorers_own_tools_inherit_the_guests_confinement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from chimera.tools.defer import _unwrapped

    ws = tmp_path / "ws"
    home = ws / ".chimera"
    home.mkdir(parents=True)
    (home / "agent.json").write_text('{"instructions": "ORION-741"}', encoding="utf-8")
    monkeypatch.setenv("CHIMERA_HOME", str(home))
    get_settings.cache_clear()

    def explorer_registry(guest: bool) -> Any:
        registry, _ = assemble_registry(
            CodeSeams(explorer=True), ws,
            Settings(CHIMERA_HOME=str(home), CHIMERA_MEMORY_BACKEND="json"), LLMGateway(),
            steps=4, surface="api:turn", guest=guest,
        )
        tool: Any = _unwrapped(registry.get("explore_repository"))
        return tool._explorer.registry_factory()

    assert "ORION-741" in _outcome(explorer_registry(False).get("read_file"), path=".chimera/agent.json"), "precondition"
    assert "ORION-741" not in _outcome(explorer_registry(True).get("read_file"), path=".chimera/agent.json")


def test_an_at_import_of_chimeras_own_env_is_not_followed_on_a_guest_turn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = tmp_path / "ws"
    client = _client(tmp_path, monkeypatch, home=ws / ".chimera")
    (ws / ".env").write_text("CHIMERA_OPENAI_KEYS=ENV-SECRET-77", encoding="utf-8")
    (ws / "CLAUDE.md").write_text("@.env", encoding="utf-8")
    monkeypatch.chdir(ws)  # the `.env` Chimera reads is the working directory's
    sid = _owner_turn(client, tmp_path)
    assert "ENV-SECRET-77" in _prompt_of(_Agent.built[-1]), "precondition: the owner's turn follows the import"
    assert "ENV-SECRET-77" not in _prompt_of(_guest_turn(client, sid))


def test_the_instruction_fallback_does_not_read_a_private_file_through_a_link(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = tmp_path / "ws"
    home = ws / ".chimera"
    monkeypatch.setenv("CHIMERA_CLAUDE_COMPAT", "0")
    client = _client(tmp_path, monkeypatch, home=home)
    (ws / "AGENTS.md").unlink()
    private = home / "instructions-private.md"
    private.write_text("FALLBACK-SECRET-88", encoding="utf-8")
    _symlink(ws / "CLAUDE.md", private)
    sid = _owner_turn(client, tmp_path)
    assert "FALLBACK-SECRET-88" in _prompt_of(_Agent.built[-1]), "precondition: the owner's turn reads the fallback"
    assert "FALLBACK-SECRET-88" not in _prompt_of(_guest_turn(client, sid))
