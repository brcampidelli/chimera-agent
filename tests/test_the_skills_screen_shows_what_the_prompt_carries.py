"""The Skills screen shows what a run is told about skills — the prompt's own bytes, not a summary.

Study 29, P7.1. "Mine" was spread over three panels (learned cards, the library, the catalogue) and
none of them answered the question a person has when a run behaves oddly: what did the agent get?
`GET /api/skills/effective` answers it from `bundles.prompt_block`, the function the agent itself
calls, and these tests hold the two to the same bytes.

Writing that test is what found the defect it guards: `Agent._bundle_context` imported
`chimera.settings`, a module that does not exist, inside a `try` that logs at debug and returns "".
From the day bundles shipped until this change, every bundle an owner switched on was missing from
every prompt, and the switch on the screen read "on" the whole time.

The update check and the forced reinstall are here too: an update asks the source once, changes
nothing, and the reinstall it leads to lands `pending` — new instructions are a new decision.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from chimera.skills import bundles

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402


def _bundle(home: Path, name: str, *, status: str = "active", **extra: Any) -> None:
    """An installed bundle on disk, as `install` leaves one — without a network."""
    root = bundles.bundles_root(home) / name
    root.mkdir(parents=True, exist_ok=True)
    (root / "SKILL.md").write_text(f"---\nname: {name}\n---\nDo the thing.\n", encoding="utf-8")
    record = {"name": name, "description": f"The {name} skill.", "status": status, **extra}
    if status == "active":
        # As `set_status` records an owner's switch: with the time it was thrown. An "active"
        # without it is one thrown while the switch reached no prompt, and reads as pending.
        record.setdefault("switched_on_at", "2026-10-03T00:00:00+00:00")
    (root / "bundle.json").write_text(json.dumps(record), encoding="utf-8")


def _client(monkeypatch: pytest.MonkeyPatch, home: Path, **env: str) -> TestClient:
    from chimera.api import build_api_app
    from chimera.config import Settings, get_settings
    from chimera.interface import ChatSession

    monkeypatch.setenv("CHIMERA_HOME", str(home))
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-x")
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    return TestClient(build_api_app(lambda: ChatSession(object()), settings=Settings()))


class _Recorder:
    """Answers every request at once and keeps what it was sent."""

    def __init__(self) -> None:
        self.sent: list[list[dict[str, Any]]] = []

    def complete(self, messages: list[Any], **kwargs: Any) -> Any:
        from chimera.providers.gateway import CompletionResult

        self.sent.append([dict(m) if isinstance(m, dict) else m.as_dict() for m in messages])
        return CompletionResult(content="done", model="fake")


def _sent_text(turn_context: bool) -> str:
    from chimera.core.agent import Agent, AgentConfig
    from chimera.tools.builtin import EchoTool
    from chimera.tools.registry import ToolRegistry

    backend = _Recorder()
    registry = ToolRegistry()
    registry.register(EchoTool())
    agent = Agent(  # type: ignore[arg-type]
        backend, registry, AgentConfig(prefix_nonce="", turn_context=turn_context)
    )
    agent.run("summarise the quarterly numbers")
    return "\n".join(str(m.get("content", "")) for m in backend.sent[0])


@pytest.mark.parametrize("turn_context", [True, False])
def test_the_text_the_screen_shows_is_byte_for_byte_what_the_run_is_sent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, turn_context: bool
) -> None:
    home = tmp_path / "home"
    _bundle(home, "pdf-forms", missing=["delegate_task"])
    _bundle(home, "spreadsheets")
    shown = _client(monkeypatch, home).get("/api/skills/effective").json()

    assert [b["name"] for b in shown["bundles"]] == ["pdf-forms", "spreadsheets"]
    assert shown["bundle_text"].startswith(bundles.PROMPT_HEADING)
    # The measure the plan registered: the screen's text is a substring of what the model got, in
    # both places the block can land (the turn context, or the system prompt when that is off).
    # This assertion is the one that was false before the import was fixed: the block was "".
    assert shown["bundle_text"] in _sent_text(turn_context)


def test_a_pending_or_switched_off_bundle_is_neither_shown_active_nor_sent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home = tmp_path / "home"
    _bundle(home, "fresh", status="pending")
    _bundle(home, "parked", status="inactive")
    shown = _client(monkeypatch, home).get("/api/skills/effective").json()

    assert shown["bundles"] == []
    assert shown["bundle_text"] == ""
    assert "fresh" not in _sent_text(True) and "parked" not in _sent_text(True)


def test_with_card_reading_off_the_screen_says_no_card_is_read(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from chimera.evolution import SkillStore
    from chimera.evolution.learned_skill import LearnedSkill

    home = tmp_path / "home"
    home.mkdir(parents=True)
    store = SkillStore(home / "skills.json")
    store.add(LearnedSkill(name="ok-card", description="d", status="active"))
    store.add(LearnedSkill(name="held-card", description="d", status="pending"))

    off = _client(monkeypatch, home).get("/api/skills/effective").json()
    # Off is the shipped default and was measured into being: an active card with zero uses means
    # "nothing consulted it", and the screen has to say so rather than list it as in use.
    assert off["cards_read"] is False and off["cards"] == [] and off["cards_k"] == 0

    on = _client(monkeypatch, home, CHIMERA_SKILL_CARDS="1").get("/api/skills/effective").json()
    assert on["cards_read"] is True
    assert on["cards"] == ["ok-card"], "a pending card is not eligible, whatever the switch says"
    assert on["cards_k"] >= 1


# --- updating -------------------------------------------------------------------------------------


class _Entry:
    name = "demo"
    description = "A demonstration skill."
    repo = "someone/skills"
    path = "skills/demo"
    ref = "main"
    license = "MIT"


def _source(
    monkeypatch: pytest.MonkeyPatch,
    head: str,
    head_date: str,
    folders: dict[str, str],
    seen: list[str],
) -> None:
    """A fake GitHub: the branch's head commit, and the skill folder's tree SHA at each commit.

    A commit missing from ``folders`` answers 404, as a commit the source no longer has does."""
    from urllib.parse import unquote

    def fake_get(url: str, *, accept: str = "", limit: int = 0) -> bytes:
        seen.append(url)
        if "/commits/" in url:
            return json.dumps({"sha": head, "commit": {"committer": {"date": head_date}}}).encode()
        if "/git/trees/" in url:
            commit = unquote(url.rsplit("/git/trees/", 1)[1]).split(":", 1)[0]
            if commit not in folders:
                raise bundles._NotFoundError(f"not found at the source: {url}")
            return json.dumps({"sha": folders[commit], "tree": []}).encode()
        raise AssertionError(f"unexpected request {url}")

    monkeypatch.setattr(bundles, "_get", fake_get)


def test_the_update_check_compares_the_skills_own_folder_and_writes_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _bundle(tmp_path, "demo", ref="a" * 40, committed_at="2026-09-01T10:00:00Z")
    before = (bundles.bundles_root(tmp_path) / "demo" / "bundle.json").read_bytes()
    seen: list[str] = []
    _source(monkeypatch, "b" * 40, "2026-09-20T10:00:00Z", {"a" * 40: "t1", "b" * 40: "t2"}, seen)

    result = bundles.check_update(_Entry(), tmp_path)

    assert (result.current_ref, result.latest_ref, result.changed) == ("a" * 40, "b" * 40, True)
    # The skill's own folder, not the repository: a shared repository moves for every other skill
    # in it, and an update badge that fires for those changes nothing in these files.
    trees = [u for u in seen if "/git/trees/" in u]
    assert trees and all("skills%2Fdemo" in u or "skills/demo" in u for u in trees)
    assert all(u.startswith("https://api.github.com/") for u in seen)
    assert (bundles.bundles_root(tmp_path) / "demo" / "bundle.json").read_bytes() == before


def test_a_merge_of_older_commits_is_an_update_whatever_the_dates_say(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The review's case. Installed on Tuesday from `main`; a PR committed on Monday that touches
    the skill is merged on Wednesday with a merge commit. The folder's newest commit by
    `commits?path=` is Monday's — before the install — and the date comparison said "up to date"
    over files that changed. A backdated commit hides the same way. Content has no clock."""
    _bundle(tmp_path, "demo", ref="a" * 40, committed_at="2026-09-22T10:00:00Z")  # Tuesday
    # The head is the Wednesday merge; what it carries in the folder is Monday's work.
    _source(
        monkeypatch, "c" * 40, "2026-09-21T09:00:00Z", {"a" * 40: "tuesday", "c" * 40: "monday"}, []
    )

    assert bundles.check_update(_Entry(), tmp_path).changed is True


def test_the_same_folder_unknowable_commits_and_a_vanished_install_are_told_apart(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Other skills moved the head, this folder did not: the same files, so nothing to update.
    _bundle(tmp_path, "demo", ref="a" * 40, committed_at="2026-09-20T10:00:00Z")
    _source(monkeypatch, "b" * 40, "2026-09-30T10:00:00Z", {"a" * 40: "t1", "b" * 40: "t1"}, [])
    assert bundles.check_update(_Entry(), tmp_path).changed is False

    # The very commit installed: no folder lookups needed.
    seen: list[str] = []
    _source(monkeypatch, "a" * 40, "2026-09-20T10:00:00Z", {}, seen)
    assert bundles.check_update(_Entry(), tmp_path).changed is False
    assert not [u for u in seen if "/git/trees/" in u]

    # The installed commit is gone from the source: unknown, not "changed" and not "the same".
    _source(monkeypatch, "b" * 40, "2026-09-30T10:00:00Z", {"b" * 40: "t2"}, [])
    assert bundles.check_update(_Entry(), tmp_path).changed is None

    # Installed without a resolvable commit (the branch name was recorded): nothing to compare.
    # Looking the branch up NOW would compare today's files with today's files and say "the same"
    # about a copy fetched from that branch at some unknown earlier point.
    _bundle(tmp_path, "demo", ref="main")
    _source(monkeypatch, "b" * 40, "2026-09-30T10:00:00Z", {"b" * 40: "t2", "main": "t2"}, [])
    assert bundles.check_update(_Entry(), tmp_path).changed is None


def test_a_skill_that_is_not_installed_has_no_update_to_check(tmp_path: Path) -> None:
    with pytest.raises(bundles.BundleError, match="not installed"):
        bundles.check_update(_Entry(), tmp_path)


def test_the_install_records_when_the_fetched_commit_was_made(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def fake_get(url: str, *, accept: str = "", limit: int = 0) -> bytes:
        if "git/trees" in url:
            return json.dumps(
                {"truncated": False, "tree": [{"path": "SKILL.md", "type": "blob", "size": 9}]}
            ).encode()
        if "/commits/" in url:
            return json.dumps(
                {"sha": "c" * 40, "commit": {"committer": {"date": "2026-09-30T08:00:00Z"}}}
            ).encode()
        return b"---\nname: demo\n---\n"

    monkeypatch.setattr(bundles, "_get", fake_get)
    record = bundles.install(_Entry(), tmp_path)
    assert (record.ref, record.committed_at) == ("c" * 40, "2026-09-30T08:00:00Z")


def test_updating_an_active_bundle_puts_it_back_to_pending(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def fake_get(url: str, *, accept: str = "", limit: int = 0) -> bytes:
        if "git/trees" in url:
            return json.dumps(
                {"truncated": False, "tree": [{"path": "SKILL.md", "type": "blob", "size": 9}]}
            ).encode()
        if "/commits/" in url:
            return json.dumps({"sha": "d" * 40}).encode()
        return b"---\nname: demo\n---\nNew text from upstream.\n"

    _bundle(tmp_path, "demo", status="active")
    monkeypatch.setattr(bundles, "_get", fake_get)

    record = bundles.install(_Entry(), tmp_path, force=True)

    # The owner switched on the text they read. The update is text they have not read, from the
    # same stranger, and carrying the old "active" across would be consent to something unseen.
    assert record.status == "pending"
    assert bundles.prompt_block(tmp_path) == ""


def test_the_update_route_names_an_unknown_skill_404(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    client = _client(monkeypatch, tmp_path / "home")
    assert client.get("/api/skills/bundles/no-such-skill/update").status_code == 404
