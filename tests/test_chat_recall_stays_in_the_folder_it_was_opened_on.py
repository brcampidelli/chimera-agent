"""``chat`` takes a ``--workspace`` and then recalled memory as if it had not.

``ChatSession._memory_search`` called ``memory.search`` without ``project``, so the default —
:data:`EVERY_PROJECT`, "do not filter at all" — applied to every terminal conversation. A note a
``solve`` wrote while working on one codebase arrived as context in a chat about another, which is
the noise the field was added to stop. The coding turn already scopes (``code_api.py``); the
terminal did not, and nothing failed.

Two halves, and the second is the one that could have gone wrong quietly: scoping must not make
memory DISAPPEAR. A fact with no project belongs everywhere — which is what every fact written
before the field existed is — so it still arrives in every folder.
"""

from __future__ import annotations

from pathlib import Path

from chimera.core.agent import AgentResult
from chimera.interface.session import ChatSession
from chimera.memory import MemoryManager, MemoryStore
from chimera.memory.models import project_key


class _Agent:
    def __init__(self) -> None:
        self.prompts: list[str] = []

    def run(self, task: str, *, on_token=None, on_tool=None) -> AgentResult:  # type: ignore[no-untyped-def]
        self.prompts.append(task)
        return AgentResult(answer="ok", steps=1, stopped_reason="final")


def _memory(tmp_path: Path, project_a: str, project_b: str) -> MemoryManager:
    memory = MemoryManager(MemoryStore(tmp_path / "m.json"))
    memory.remember("the retry cap here is six", project=project_a)
    memory.remember("the retry cap here is nine", project=project_b)
    memory.remember("Alex prefers absolute imports", project=None)
    return memory


def test_a_fact_stored_for_another_project_is_not_recalled(tmp_path: Path) -> None:
    a, b = str(tmp_path / "a"), str(tmp_path / "b")
    agent = _Agent()
    session = ChatSession(agent, memory=_memory(tmp_path, a, b), gate=None, project=a)

    session.send("what is the retry cap?")

    assert "six" in agent.prompts[0]
    assert "nine" not in agent.prompts[0]


def test_a_fact_with_no_project_is_still_recalled(tmp_path: Path) -> None:
    """Scoping narrows; it must not hide. Every fact written before the field existed has no
    project, so reading this wrong would look like memory loss on upgrade."""
    a, b = str(tmp_path / "a"), str(tmp_path / "b")
    agent = _Agent()
    session = ChatSession(agent, memory=_memory(tmp_path, a, b), gate=None, project=a)

    session.send("any rule about imports?")

    assert "absolute imports" in agent.prompts[0]


def test_the_default_session_still_recalls_everything(tmp_path: Path) -> None:
    """Every caller that is not a folder — the gateway, the benchmarks, the OpenAI-compatible
    endpoint — keeps the behaviour it had. The narrowing is something a surface opts into."""
    a, b = str(tmp_path / "a"), str(tmp_path / "b")
    agent = _Agent()
    ChatSession(agent, memory=_memory(tmp_path, a, b), gate=None).send("what is the retry cap?")

    assert "six" in agent.prompts[0] and "nine" in agent.prompts[0]


def test_a_folder_is_filed_under_one_string_whoever_writes_it(tmp_path: Path) -> None:
    """The writer and the reader have to agree, and they did not.

    ``chimera solve`` wrote ``project=str(Path(workspace))`` — literally ``"."`` for the default
    run — while the coding turn read ``str(Path(req.workspace).expanduser().resolve())``. Two
    strings for one folder means the scoped read matches nothing it wrote, and the symptom is not
    an error: it is memory that is simply never recalled.
    """
    assert project_key(".") == project_key(Path.cwd())
    assert project_key(None) is None
    assert project_key("") is None
    assert Path(str(project_key("."))).is_absolute()


def test_the_written_project_matches_what_a_chat_in_that_folder_reads(tmp_path: Path) -> None:
    """The end-to-end version of the line above: write as `solve` does, read as `chat` does."""
    memory = MemoryManager(MemoryStore(tmp_path / "m.json"))
    memory.remember("this repo pins ruff", project=project_key(tmp_path))

    agent = _Agent()
    ChatSession(agent, memory=memory, gate=None, project=project_key(tmp_path)).send(
        "does this repo pin ruff?"
    )

    assert "pins ruff" in agent.prompts[0]
