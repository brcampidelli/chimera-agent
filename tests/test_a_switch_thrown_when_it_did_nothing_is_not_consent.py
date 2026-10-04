"""A bundle switched on while a switched-on bundle reached no prompt waits for a new switch.

Review of study 29, P7.1, finding 2. Until P7.1 the agent's import of the bundle block raised and
was swallowed: the switch said "on" and nothing reached any prompt. Fixing the import made every
`active` already on disk reach the system prompt of every run — chat, Code, scheduled jobs, the
bots — on the first run after the upgrade, without anyone deciding it. Some of those switches were
the owner's, thrown to an effect that did not exist; some came from a bridge client of the
`operate` tier, whose route defaulted to `active` until it was narrowed to "off only". A switch
thrown when it did nothing is not consent to what it does now.

So `set_status` records when an owner's switch went on, and an `active` without that record reads
as `pending` (flagged `reconfirm`) and reaches nothing until it is switched on again.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from chimera.skills import bundles

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402


def _old_switch(home: Path, name: str) -> Path:
    """A bundle as the disk holds one switched on before this change: `active`, and nothing else."""
    root = bundles.bundles_root(home) / name
    root.mkdir(parents=True, exist_ok=True)
    (root / "SKILL.md").write_text(f"---\nname: {name}\n---\n", encoding="utf-8")
    meta = root / "bundle.json"
    meta.write_text(
        json.dumps({"name": name, "description": f"The {name} skill.", "status": "active"}),
        encoding="utf-8",
    )
    return meta


def _client(monkeypatch: pytest.MonkeyPatch, home: Path) -> TestClient:
    from chimera.api import build_api_app
    from chimera.config import Settings, get_settings
    from chimera.interface import ChatSession

    monkeypatch.setenv("CHIMERA_HOME", str(home))
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-x")
    get_settings.cache_clear()
    return TestClient(build_api_app(lambda: ChatSession(object()), settings=Settings()))


def _prompt(monkeypatch: pytest.MonkeyPatch, home: Path) -> str:
    from chimera.config import get_settings
    from chimera.core.agent import Agent, AgentConfig
    from chimera.tools.registry import ToolRegistry

    monkeypatch.setenv("CHIMERA_HOME", str(home))
    get_settings.cache_clear()
    return Agent(object(), ToolRegistry(), AgentConfig())._bundle_context()  # type: ignore[arg-type]


def test_an_old_switch_reaches_no_prompt_and_says_why(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    meta = _old_switch(tmp_path, "pdf-forms")
    before = meta.read_bytes()

    assert _prompt(monkeypatch, tmp_path) == ""
    client = _client(monkeypatch, tmp_path)
    listed = {b["name"]: b for b in client.get("/api/skills/bundles").json()}
    assert listed["pdf-forms"]["status"] == "pending" and listed["pdf-forms"]["reconfirm"] is True
    effective = client.get("/api/skills/effective").json()
    assert effective["bundle_text"] == "" and effective["reconfirm"] == ["pdf-forms"]
    # Read, not rewritten: the decision is the owner's next switch, not this read.
    assert meta.read_bytes() == before


def test_skill_view_does_not_read_an_old_switch_either(tmp_path: Path) -> None:
    """The prompt line was not the only reader. `skill_view` (feat/skill-upload) read the switch
    from `bundle.json` itself, so an old `active` the Skills screen lists as waiting for the owner
    was still readable by name — and a name is guessable whatever the prompt line says."""
    from chimera.skills.aliases import SkillView

    _old_switch(tmp_path, "pdf-forms")
    out = SkillView(bundles.bundles_root(tmp_path)).run(name="pdf-forms")
    assert out.startswith("error:") and "switched off" in out

    bundles.set_status("pdf-forms", tmp_path, "active")
    assert not SkillView(bundles.bundles_root(tmp_path)).run(name="pdf-forms").startswith("error:")


def test_switching_it_on_again_is_what_sends_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _old_switch(tmp_path, "pdf-forms")
    client = _client(monkeypatch, tmp_path)

    on = client.post("/api/skills/bundles/pdf-forms/status", json={"status": "active"}).json()

    assert on["status"] == "active" and on["reconfirm"] is False
    assert "pdf-forms" in _prompt(monkeypatch, tmp_path)
    assert client.get("/api/skills/effective").json()["reconfirm"] == []


def test_off_clears_the_record_and_on_writes_it_again(tmp_path: Path) -> None:
    meta = _old_switch(tmp_path, "pdf-forms")

    bundles.set_status("pdf-forms", tmp_path, "active")
    assert json.loads(meta.read_text(encoding="utf-8"))["switched_on_at"]
    bundles.set_status("pdf-forms", tmp_path, "inactive")
    assert "switched_on_at" not in json.loads(meta.read_text(encoding="utf-8"))
    assert [b.name for b in bundles.active(tmp_path)] == []


def test_the_read_only_flag_is_never_written_to_disk(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    class _Entry:
        name = "demo"
        description = "A demonstration skill."
        repo = "someone/skills"
        path = "skills/demo"
        ref = "main"
        license = "MIT"

    def fake_get(url: str, *, accept: str = "", limit: int = 0) -> bytes:
        if "git/trees" in url:
            return json.dumps(
                {"truncated": False, "tree": [{"path": "SKILL.md", "type": "blob", "size": 9}]}
            ).encode()
        if "/commits/" in url:
            return json.dumps({"sha": "d" * 40}).encode()
        return b"---\nname: demo\n---\n"

    monkeypatch.setattr(bundles, "_get", fake_get)
    bundles.install(_Entry(), tmp_path)

    stored = json.loads((bundles.bundles_root(tmp_path) / "demo" / "bundle.json").read_text("utf-8"))
    assert "reconfirm" not in stored and stored["status"] == "pending"
