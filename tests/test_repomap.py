"""Tests for the repo-map (M13 A2) — structural workspace digest via AST."""

from __future__ import annotations

from pathlib import Path

from chimera.core.agent import AgentResult
from chimera.core.autonomous import AutonomousAgent, AutonomousConfig
from chimera.core.repomap import build_repo_map


def _write(root: Path, rel: str, content: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_lists_files_with_top_level_symbols(tmp_path: Path) -> None:
    _write(tmp_path, "app/core.py", "def run():\n    pass\n\nclass Engine:\n    def go(self): ...\n")
    _write(tmp_path, "util.py", "async def fetch():\n    return 1\n")
    digest = build_repo_map(tmp_path)
    assert "app/core.py: run(), Engine" in digest  # top-level func + class; method 'go' excluded
    assert "util.py: fetch()" in digest


def test_empty_project_is_empty(tmp_path: Path) -> None:
    (tmp_path / "notes.txt").write_text("no python here", encoding="utf-8")
    assert build_repo_map(tmp_path) == ""


def test_prunes_default_noise_dirs(tmp_path: Path) -> None:
    _write(tmp_path, "real.py", "def keep(): ...\n")
    _write(tmp_path, "__pycache__/junk.py", "def hidden(): ...\n")
    _write(tmp_path, ".venv/lib/dead.py", "def dead(): ...\n")
    digest = build_repo_map(tmp_path)
    assert "real.py" in digest
    assert "junk" not in digest and "dead" not in digest


def test_respects_gitignore(tmp_path: Path) -> None:
    _write(tmp_path, ".gitignore", "secret.py\nignored_dir/\n")
    _write(tmp_path, "public.py", "def ok(): ...\n")
    _write(tmp_path, "secret.py", "def leak(): ...\n")
    _write(tmp_path, "ignored_dir/inside.py", "def nope(): ...\n")
    digest = build_repo_map(tmp_path)
    assert "public.py" in digest
    assert "secret.py" not in digest and "ignored_dir" not in digest


def test_truncates_to_budget(tmp_path: Path) -> None:
    for i in range(50):
        _write(tmp_path, f"mod_{i:02d}.py", f"def f_{i}(): ...\n")
    digest = build_repo_map(tmp_path, max_chars=120)
    assert "more file(s) omitted" in digest
    assert len(digest) < 250  # bounded well under the full listing


def test_survives_syntax_error(tmp_path: Path) -> None:
    _write(tmp_path, "broken.py", "def (( not valid python\n")
    digest = build_repo_map(tmp_path)
    assert "broken.py" in digest  # listed without symbols, no crash


def test_agent_injects_repo_map_into_worker_prompt(tmp_path: Path) -> None:
    _write(tmp_path, "widget.py", "def assemble_widget():\n    return 1\n")

    class _Worker:
        def __init__(self) -> None:
            self.prompts: list[str] = []

        def run(self, task: str) -> AgentResult:
            self.prompts.append(task)
            return AgentResult(answer="ok", steps=1, transcript=[], stopped_reason="done")

    worker = _Worker()
    agent = AutonomousAgent(
        worker,
        spine_workspace=tmp_path,
        repo_map=True,
        config=AutonomousConfig(max_attempts=1, use_planner=False, use_manager=False),
    )
    agent.run("change the widget")
    assert "assemble_widget()" in worker.prompts[0]  # the map reached the worker's context
