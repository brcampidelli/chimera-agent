"""The skills a run's prompt lists come from the settings the run was ASSEMBLED with.

Review of study 29, P7.1/P7.6. `Agent._bundle_context` re-read `get_settings()` — the process's
`.env` and environment — while `assemble_registry` and `GET /api/skills/effective` read the app's
own settings (`build_api_app(settings=...)`, which is how a bench arm or a test runs two
configurations side by side). With the two disagreeing, the prompt listed another home's bundles
than the screen claimed to show "byte for byte", and a pack switched on for arm B narrowed the
tools of arm B and not its skills — two numbers measured on different rulers that read as one.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from chimera.api.code_api import CodeSeams, assemble_registry
from chimera.config import Settings, get_settings
from chimera.core import project_pack as packs
from chimera.core.agent import Agent, AgentConfig
from chimera.integrations import mcp_pool
from chimera.skills import bundles

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402


def _bundle(home: Path, name: str) -> None:
    root = bundles.bundles_root(home) / name
    root.mkdir(parents=True, exist_ok=True)
    (root / "SKILL.md").write_text(f"---\nname: {name}\n---\n", encoding="utf-8")
    (root / "bundle.json").write_text(
        # Switched on the way `set_status` records it (with the time), as an owner's switch is.
        json.dumps(
            {"name": name, "description": f"The {name} skill.", "status": "active", "switched_on_at": "2026-10-03T00:00:00+00:00"}
        ),
        encoding="utf-8",
    )


def test_an_app_with_its_own_settings_prompts_the_skills_its_screen_shows(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from chimera.api import build_api_app
    from chimera.interface import ChatSession

    app_home, process_home, ws = tmp_path / "app", tmp_path / "process", tmp_path / "ws"
    ws.mkdir()
    _bundle(app_home, "pdf-forms")
    _bundle(app_home, "supabase-admin")
    _bundle(process_home, "only-in-the-process-home")
    # The arm: its own home, packs on, a pack accepted that keeps one bundle. The process: another
    # home, packs off — what `.env` says on the machine the bench runs on.
    (ws / packs.PACK_PATH).parent.mkdir(parents=True)
    (ws / packs.PACK_PATH).write_text(json.dumps({"skills": ["pdf-forms"]}), encoding="utf-8")
    packs.accept(app_home, ws, packs.read_pack(ws).digest)
    monkeypatch.setenv("CHIMERA_HOME", str(process_home))
    monkeypatch.setenv("CHIMERA_PROJECT_PACK", "0")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-x")
    get_settings.cache_clear()
    monkeypatch.setattr(mcp_pool, "connectors", lambda _s: None)
    arm = Settings(CHIMERA_HOME=str(app_home), CHIMERA_PROJECT_PACK=True)  # type: ignore[call-arg]

    registry, _ = assemble_registry(CodeSeams(), ws, arm, object(), steps=3)
    prompt = Agent(object(), registry, AgentConfig(project_root=ws))._bundle_context()  # type: ignore[arg-type]
    client = TestClient(build_api_app(lambda: ChatSession(object()), settings=arm))
    screen = client.get("/api/skills/effective", params={"project": str(ws)}).json()

    assert "only-in-the-process-home" not in prompt, "the prompt read the process's home"
    assert "pdf-forms" in prompt and "supabase-admin" not in prompt
    assert screen["bundle_text"] and screen["bundle_text"] in prompt
