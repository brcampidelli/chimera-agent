"""The web research sub-agent (study 25, S12): a setting, read-only, and its citations checked.

The prompt asks the sub-agent to cite only URLs it saw in a tool result. A prompt can only ask, so
the harness logs every tool result the sub-agent is shown and checks every URL in its final answer
against that log. The caller gets the answer with a receipt naming any URL no tool result contained.
Everything here runs offline, with fake tools and a scripted model.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from chimera.config import Settings, get_settings
from chimera.core import research as research_module
from chimera.core.agent import PARALLEL_READ_TOOLS, ToolActivity
from chimera.core.research import (
    RESEARCH_SYSTEM,
    RESEARCH_TOOLS,
    ResearchWebTool,
    SourceLog,
    WebResearcher,
    check_citations,
    normalise_url,
    web_research_registry,
)
from chimera.governance.ledger import EXEC_TOOLS, FETCH_TOOLS, SIDE_EFFECT_TOOLS, WRITE_TOOLS
from chimera.governance.ledger_tool import FENCE_OPEN, fence
from chimera.providers.gateway import CompletionResult, MessageLike, ToolCall
from chimera.tools.base import Tool, is_untrusted_output
from chimera.tools.registry import ToolRegistry

READ = "https://en.wikipedia.org/wiki/Mercury_(planet)"
INVENTED = "https://example.org/a-page-nobody-fetched"


def _seen(url: str, observation: str, *, name: str = "http_get", ok: bool = True) -> ToolActivity:
    return ToolActivity(name, {"url": url}, ok, observation)


def test_the_check_flags_an_invented_url_and_passes_one_it_read() -> None:
    log = SourceLog()
    log.record(_seen(READ, fence(f"[200] {READ}\nMercury is the smallest planet.")))
    answer = (
        f"Mercury is the smallest planet ([source]({READ})). It has no moons, see {INVENTED}."
    )

    check = check_citations(answer, log)

    assert check.cited == (READ, INVENTED)
    assert check.verified == (READ,)
    assert check.unverified == (INVENTED,)
    assert INVENTED in check.receipt() and "1 of 2 cited URLs" in check.receipt()


def test_a_url_seen_only_as_a_link_in_a_result_counts_as_read() -> None:
    """An id or a URL a search result listed came from tool output, which is all the rule asks."""
    log = SourceLog()
    log.record(ToolActivity("web_search", {"query": "mercury"}, True, f"- Mercury — {READ}\n  ..."))
    assert check_citations(f"See {READ}.", log).unverified == ()


def test_a_failed_fetch_is_not_a_source() -> None:
    """A 404 page and an error both echo the address they could not read."""
    missing = "https://en.wikipedia.org/wiki/No_such_page"
    broken = "https://broken.example/x"
    log = SourceLog()
    log.record(_seen(missing, f"[404] {missing}\nWikipedia does not have an article {missing}"))
    log.record(_seen(broken, fence(f"error: request failed for {broken}")))
    page = f"# Oops\n[source: http · {broken} · status 503]\n\n{broken}"
    log.record(_seen(broken, page, name="scrape"))

    check = check_citations(f"It is at {missing} and {broken}.", log)

    assert check.unverified == (missing, broken)
    assert log.failures == 3


def test_two_spellings_of_one_page_are_one_page() -> None:
    same = [
        "https://en.wikipedia.org/wiki/J%C3%B8rn_Utzon",
        "http://en.wikipedia.org/wiki/Jørn_Utzon/",
        "https://en.m.wikipedia.org/wiki/Jørn_Utzon#Early_life",
    ]
    assert len({normalise_url(url) for url in same}) == 1
    assert normalise_url("https://arxiv.org/abs/2401.01234v3") == normalise_url(
        "http://arxiv.org/abs/2401.01234"
    )
    # The query and the path's case still pick a different document.
    assert normalise_url("https://x.org/a?id=1") != normalise_url("https://x.org/a?id=2")
    assert normalise_url("https://x.org/Page") != normalise_url("https://x.org/page")


class _Page(Tool):
    """A fetch tool with a fixed web of pages."""

    def __init__(self, name: str, pages: dict[str, str]) -> None:
        self.name = name
        self.description = "fetch a page"
        self.parameters = {"type": "object", "properties": {"url": {"type": "string"}}}
        self.pages = pages

    def run(self, **kwargs: Any) -> str:
        url = str(kwargs.get("url", ""))
        return f"[200] {url}\n{self.pages[url]}" if url in self.pages else f"[404] {url}\nnot found"


class _Script:
    """A model that fetches one page, then answers citing it and a URL it never saw."""

    def __init__(self) -> None:
        self.sent: list[list[MessageLike]] = []

    def complete(self, messages: list[MessageLike], **kwargs: object) -> CompletionResult:
        self.sent.append(list(messages))
        if len(self.sent) == 1:
            call = ToolCall(id="1", name="http_get", arguments={"url": READ})
            return CompletionResult(content="", model="fake", tool_calls=[call])
        return CompletionResult(content=f"Mercury. Source: {READ}. Also {INVENTED}.", model="fake")


def test_the_receipt_reaches_the_caller_after_the_answer() -> None:
    source = ToolRegistry()
    source.register(_Page("http_get", {READ: "Mercury is the smallest planet."}))
    backend = _Script()

    tool = ResearchWebTool(backend, source)
    out = tool.run(question="Which planet is smallest?", thoroughness="quick")

    assert backend.sent[0][0]["content"].startswith(RESEARCH_SYSTEM)
    assert "Thoroughness: quick" in backend.sent[0][-1]["content"]
    # The page reached the sub-agent inside the data fence, even though the tool did not fence it.
    assert backend.sent[1][-1]["content"].startswith(FENCE_OPEN)
    answer, receipt = out.rsplit("\n\n", 1)
    assert answer == f"Mercury. Source: {READ}. Also {INVENTED}."
    assert receipt.startswith("[source check: 1 of 2 cited URLs")
    assert receipt.endswith(f"them: {INVENTED}]")


def test_the_registry_is_read_only(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Only tools the product itself lists as read-only, never one that writes, runs or sends."""
    from chimera.tools.builtin import default_registry

    assert RESEARCH_TOOLS <= PARALLEL_READ_TOOLS & FETCH_TOOLS
    assert not RESEARCH_TOOLS & (WRITE_TOOLS | EXEC_TOOLS | SIDE_EFFECT_TOOLS)
    assert not RESEARCH_TOOLS & {"browser", "crawl", "download_media", "extract"}

    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("TAVILY_API_KEY", "test-not-a-key")
    get_settings.cache_clear()
    everything = default_registry(tmp_path, host_exec_confirm=None)
    drawn = web_research_registry(everything)
    fresh = web_research_registry()
    assert set(drawn.names()) == set(fresh.names()) == RESEARCH_TOOLS
    assert all(is_untrusted_output(tool) for tool in drawn.tools())


def test_a_tool_denied_to_the_main_agent_is_denied_to_the_researcher() -> None:
    """Drawn late from the caller's registry: whatever the lists left there is all it gets."""
    registry = ToolRegistry()
    registry.register(_Page("scrape", {}))
    tool = ResearchWebTool(_Script(), lambda: registry)
    registry = ToolRegistry()  # rebound after registration, as the caller's governance does
    refused = "error: no web tool is available to research with in this session"
    assert tool.run(question="q") == refused


def test_thoroughness_sets_the_research_step_ceiling(monkeypatch: pytest.MonkeyPatch) -> None:
    ceilings: list[int] = []

    class _Agent:
        def __init__(self, backend: Any, tools: Any, config: Any) -> None:
            ceilings.append(config.max_steps)

        def run(self, task: str, **kwargs: Any) -> Any:
            return type("R", (), {
                "answer": "", "steps": 0, "tool_calls_made": 0, "prompt_tokens": 0,
                "completion_tokens": 0, "usd": 0.0, "stopped_reason": "final",
            })()

    monkeypatch.setattr(research_module, "Agent", _Agent)
    source = ToolRegistry()
    source.register(_Page("http_get", {}))
    researcher = WebResearcher(_Script(), source)
    for level in ("quick", "medium", "thorough", "whatever"):
        researcher.research("q", level)
    assert ceilings == [6, 12, 24, 12]


def test_its_answer_is_web_content_to_the_caller() -> None:
    assert is_untrusted_output(ResearchWebTool(_Script(), ToolRegistry()))


def test_research_is_off_unless_switched_on(tmp_path: Path) -> None:
    from chimera.api.code_api import CodeSeams, assemble_registry
    from chimera.providers import LLMGateway

    ws = tmp_path / "ws"
    ws.mkdir()
    home = str(tmp_path / "home")

    def names(**env: str) -> set[str]:
        settings = Settings(CHIMERA_HOME=home, **env)
        registry, _ = assemble_registry(CodeSeams(), ws, settings, LLMGateway(), steps=8)
        return set(registry.names())

    assert "research_web" not in names()
    assert "research_web" in names(CHIMERA_RESEARCH_AGENT="1")
    denied = names(CHIMERA_RESEARCH_AGENT="1", CHIMERA_TOOL_DENYLIST="research_web")
    assert "research_web" not in denied
