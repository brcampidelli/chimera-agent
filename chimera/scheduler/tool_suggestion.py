"""A suggested minimal tool set for a scheduled job, worked out from what its prompt says.

Every job without ``tools`` carries all of the registry's schemas on every step, and offers the
model things the job has no business doing. The owner knows the job; this only reads its prompt and
says what a job like that usually needs, so the screen and the CLI can show a starting point.

A SUGGESTION and nothing else. It is never applied: a missing tool fails a run quietly (the only
trace is a worse answer), and deciding that a job does not need one is the owner's call. It is
deterministic keyword matching, deliberately not a model call: a model asked to pick tools for a
job is spending money to save money, and the answer would differ between two looks at one prompt.
"""

from __future__ import annotations

import re

#: What a job is assumed to need whatever it says: it has to be able to look at what is in front of
#: it. Read-only on purpose — nothing here writes, runs a command or reaches the network.
_BASE: tuple[str, ...] = ("read_file", "list_dir", "grep", "glob")

#: Prompt words (matched as whole words, case-insensitive) -> the tools they suggest. Only names
#: that exist in ``default_registry`` (a test holds the two together).
_RULES: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
    (("http", "https", "url", "feed", "rss", "api", "endpoint", "price", "prices", "site", "website"),
     ("http_get",)),
    (("search", "news", "headline", "headlines", "research"), ("scrape", "http_get")),
    (("scrape", "crawl", "page", "pages"), ("scrape", "crawl")),
    (("pdf", "docx", "spreadsheet", "xlsx", "document", "documents"), ("read_document",)),
    (("write", "save", "report", "update", "create", "generate", "append"), ("write_file",)),
    (("edit", "fix", "refactor", "patch", "rename"), ("edit_file", "apply_patch")),
    (("test", "tests", "build", "lint", "deploy", "run", "command", "script", "git", "commit"),
     ("run_shell",)),
    (("chart", "plot", "graph"), ("render_chart",)),
    (("youtube", "video", "transcript"), ("youtube_transcript",)),
    (("arxiv", "paper", "papers"), ("arxiv_search",)),
    (("pull", "pr"), ("open_pull_request",)),
)

_WORD = re.compile(r"[a-z0-9_]+")


def suggest_tools(action: str) -> list[str]:
    """The tools a job with this prompt probably needs, in a stable order, without duplicates.

    Always the read-only base, plus whatever the prompt's words point at. Never empty, and never
    more than the registry offers: a job that says nothing gets the base and nothing that can write.
    """
    words = set(_WORD.findall(action.lower()))
    chosen: list[str] = list(_BASE)
    for triggers, tools in _RULES:
        if words.intersection(triggers):
            chosen.extend(t for t in tools if t not in chosen)
    return chosen
