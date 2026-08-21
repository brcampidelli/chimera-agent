"""Speaking the other agent's tool vocabulary, where the capability is really the same.

The catalogued skills were written against a different runtime, and a large part of what makes
them "not portable" is a naming difference and nothing else: their ``web_extract`` is our
``scrape``, their ``search_files`` is our ``grep``. A skill that says *"use search_files to find
the error string"* is giving good advice to an agent that has never heard the word.

So this is a translation layer, and it is deliberately NOT a table of name pairs. Measuring the
eighty-two bodies first showed why: ``web_extract(urls=["a", "b"])`` takes a LIST where ``scrape``
takes one url, and ``skill_view(name=, file_path=)`` resolves a file inside a named skill rather
than a path. A name-only alias would have produced tools that exist and fail on their first call,
which is worse than tools that are absent — an absent tool makes the model adapt, a broken one
makes it retry.

Three properties this has to hold, in order of how badly getting them wrong would hurt:

1. **An alias is never a hole in the allowlist.** Adapters are built *from* a registry, wrapping
   the tool that is in it. If posture, an allowlist or a missing credential removed ``scrape``,
   then ``web_extract`` does not exist either — it cannot, because there is nothing to wrap.
2. **Taint survives the wrapper.** :func:`chimera.tools.base.is_untrusted_output` resolves through
   ``.inner``, and its own docstring says a wrapper that forgets is the difference between a
   defended run and an undefended one. Every adapter sets ``.inner`` and mirrors the flag.
3. **What we cannot translate is SAID, not silently dropped.** ``delegate_task`` and
   ``vision_analyze`` have no equivalent here. A skill that calls them should make the agent
   say so, not invent a tool — so those names are reported to the prompt as absent, with what
   they would have done.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from chimera.tools.base import Tool, is_untrusted_output
from chimera.tools.registry import ToolRegistry

__all__ = [
    "MISSING",
    "SkillView",
    "adapt_registry",
    "install_into",
    "foreign_names",
    "missing_names",
    "translated_names",
]

#: How many URLs one `web_extract` call may expand into. Its callers pass two or three; the cap is
#: here because the argument is a list from a model and a list from a model can be any length.
_MAX_URLS = 8


@dataclass(frozen=True)
class _Spec:
    """One translation: which of our tools does the work, and how the arguments move across."""

    target: str
    description: str


#: Upstream name → the tool of ours that does the same job. Only entries where the CAPABILITY
#: matches; a rough approximation would be worse than nothing, because the skill's instructions
#: were written expecting the real behaviour.
_SPECS: dict[str, _Spec] = {
    "web_extract": _Spec("scrape", "Fetch one or more web pages and return their text."),
    "search_files": _Spec("grep", "Search file contents by regular expression."),
    "read_text": _Spec("read_file", "Read a text file."),
}

#: Names with no equivalent here, and what a skill was expecting them to do. Present as data
#: rather than as an omission: the agent is told, which is what stops it inventing one.
MISSING: dict[str, str] = {
    # What the skill expected, and — where the capability exists here under a different shape —
    # where to find it. "Not available" and "not available AS A TOOL" are different sentences, and
    # the second is the useful one: Chimera schedules and remembers, just not from inside a turn.
    "delegate_task": "run a sub-agent on part of the task",
    "vision_analyze": "describe or read an image",
    "browser_navigate": "open a page in a browser session it drives",
    "browser_vision": "look at the rendered page as an image",
    "kanban_show": "read a card from the upstream agent's board",
    "kanban_comment": "comment on a card from the upstream agent's board",
    "kanban_complete": "close a card on the upstream agent's board",
    "kanban_request_changes": "send a card back on the upstream agent's board",
    "kanban_block": "block a card on the upstream agent's board",
    "skill_manage": "install or edit skills from inside a run — here that is `chimera skills-*`",
    "session_search": "search the upstream agent's own session history",
    "cronjob": "schedule itself to run again — here that is `chimera cron`, set up outside the run",
    "memory": "read and write a memory store — here that is `chimera memory`, outside the run",
    "todo": "keep a task list inside the run",
    "process": "manage long-running background processes",
    "computer_use": "drive the desktop through an accessibility tree",
}


class _Adapted(Tool):
    """One of our tools, reachable by the name a catalogued skill knows it as."""

    def __init__(self, name: str, description: str, inner: Tool) -> None:
        self.name = name
        self.description = description
        self.parameters = inner.parameters
        #: Read by :func:`~chimera.tools.base.is_untrusted_output`, which walks this chain. Set,
        #: and the flag mirrored below, because a wrapper that drops it disarms the taint layer
        #: without anything failing.
        self.inner = inner
        self.untrusted_output = is_untrusted_output(inner)

    def _translate(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        return kwargs

    def run(self, **kwargs: Any) -> str:
        return self.inner.run(**self._translate(kwargs))


class _WebExtract(_Adapted):
    """``web_extract(urls=[...])`` over a tool that fetches one page at a time."""

    def __init__(self, inner: Tool) -> None:
        super().__init__("web_extract", "Fetch one or more web pages and return their text.", inner)
        self.parameters = {
            "type": "object",
            "properties": {
                "urls": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Pages to fetch.",
                },
                "url": {"type": "string", "description": "A single page, if you only have one."},
            },
        }

    def run(self, **kwargs: Any) -> str:
        raw = kwargs.get("urls") or kwargs.get("url") or []
        urls = [str(u) for u in (raw if isinstance(raw, list) else [raw]) if str(u).strip()]
        if not urls:
            return "error: web_extract needs at least one url."
        if len(urls) > _MAX_URLS:
            # Truncated loudly. A silently shortened list reads as "those pages had nothing".
            return (
                f"error: web_extract was given {len(urls)} urls; this adapter fetches at most "
                f"{_MAX_URLS} per call. Split the list."
            )
        if len(urls) == 1:
            return self.inner.run(url=urls[0])
        # Each page labelled with the URL it came from — a concatenation without them is a wall
        # of text the caller cannot attribute.
        return "\n\n".join(f"## {url}\n{self.inner.run(url=url)}" for url in urls)


class _SearchFiles(_Adapted):
    """``search_files(pattern, path=, file_glob=)`` over ``grep(pattern, path=, glob=)``."""

    def __init__(self, inner: Tool) -> None:
        super().__init__("search_files", "Search file contents by regular expression.", inner)

    def _translate(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        out = dict(kwargs)
        # The only real difference between the two signatures, and the whole reason a name-only
        # alias would have produced a tool that exists and fails.
        if "file_glob" in out:
            out["glob"] = out.pop("file_glob")
        return out


class SkillView(Tool):
    """Read a file that came with an installed skill.

    Not an alias, and the distinction matters: ``read_file`` is rooted in the WORKSPACE and refuses
    anything outside it, which is right and which means it cannot open a bundle — those live in the
    agent's home. So the progressive-disclosure story shipped broken: the prompt told the agent to
    read a path its own file tool would not resolve. This is the tool that makes an installed skill
    readable at all, rooted at the bundles directory and refusing to leave it.

    Its output is marked untrusted. These files were downloaded from a stranger's repository, and
    a reference file is not the same thing as the SKILL.md a person read before switching the skill
    on. If that proves noisy the answer is to approve at a finer grain, not to relabel where the
    text came from.
    """

    name = "skill_view"
    description = (
        "Read a file that came with an installed skill — its SKILL.md, or something under "
        "references/, scripts/ or templates/. Args: name (the skill), file_path (default SKILL.md)."
    )
    untrusted_output = True
    parameters = {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "The installed skill's name."},
            "file_path": {
                "type": "string",
                "description": "Path inside that skill, e.g. references/forms.md. Default SKILL.md.",
            },
        },
        "required": ["name"],
    }

    #: A reference file can be long, and this lands in a model's context.
    MAX_CHARS = 60_000

    def __init__(self, root: Path) -> None:
        self._root = Path(root)

    def run(self, **kwargs: Any) -> str:
        name = str(kwargs.get("name") or "").strip()
        inner_path = str(kwargs.get("file_path") or "SKILL.md").strip()
        # A name, not a path. The arguments come from a model reading a stranger's instructions.
        if not name or "/" in name or "\\" in name or name.startswith("."):
            return f"error: {name!r} is not an installed skill name."
        base = (self._root / name).resolve()
        if not base.is_dir():
            return f"error: no installed skill named {name!r}."
        target = (base / inner_path).resolve()
        if not str(target).startswith(str(base)):
            return f"error: {inner_path!r} is outside the {name} skill."
        if not target.is_file():
            return f"error: {name}/{inner_path} is not there."
        text = target.read_text(encoding="utf-8", errors="replace")
        if len(text) > self.MAX_CHARS:
            # Truncation said out loud: silently half a procedure is a procedure that goes wrong
            # at step nine with no sign of why.
            return text[: self.MAX_CHARS] + f"\n\n[truncated at {self.MAX_CHARS} characters]"
        return text


def install_into(registry: ToolRegistry, *, bundles_root: Path | None = None) -> list[str]:
    """Add the catalogued skills' vocabulary to ``registry`` in place; return what was added.

    Built FROM the registry: each adapter wraps a tool that is already in it, so anything posture,
    an allowlist or a missing credential took out stays out. An alias cannot reach a tool its own
    registry does not have — that is the property that keeps this from being a way around the gate.
    """
    present = {tool.name: tool for tool in registry.tools()}
    added: list[str] = []

    for alias, spec in _SPECS.items():
        if alias in present:
            continue  # the real thing is already here under that name — leave it alone
        inner = present.get(spec.target)
        if inner is None:
            continue
        if alias == "web_extract":
            registry.register(_WebExtract(inner))
        elif alias == "search_files":
            registry.register(_SearchFiles(inner))
        else:
            registry.register(_Adapted(alias, spec.description, inner))
        added.append(alias)

    # Not an alias of anything in `registry`: `read_file` is rooted in the workspace and a bundle
    # lives in the home directory, so this is a capability of its own, scoped to that directory.
    if bundles_root is not None and "skill_view" not in present:
        registry.register(SkillView(bundles_root))
        added.append("skill_view")
    return added


def adapt_registry(registry: ToolRegistry, *, bundles_root: Path | None = None) -> ToolRegistry:
    """A copy of ``registry`` that also answers to the names the catalogued skills use."""
    out = ToolRegistry()
    for tool in registry.tools():
        out.register(tool)
    install_into(out, bundles_root=bundles_root)
    return out


# ---------------------------------------------------------------------------------------------
# Reading a skill's vocabulary


def foreign_names(body: str) -> list[str]:
    """The tool names a SKILL.md calls that are not ours — translated or not.

    Matches a call (``name(``) and a prose mention (`` `name` ``), because these bodies do both and
    the second is where a skill says "use search_files to find it". Restricted to names we know
    about: a generous pattern over other people's markdown would otherwise collect every snake_case
    function in every code sample.
    """
    import re

    known = set(_SPECS) | set(MISSING)
    found = set()
    for pattern in (r"\b([a-z][a-z0-9_]+)\s*\(", r"`([a-z][a-z0-9_]+)`"):
        for name in re.findall(pattern, body):
            if name in known:
                found.add(name)
    return sorted(found)


def translated_names(names: list[str]) -> list[str]:
    """Of those, the ones this module can answer to."""
    return sorted(n for n in names if n in _SPECS)


def missing_names(names: list[str]) -> list[str]:
    """Of those, the ones nothing here provides."""
    return sorted(n for n in names if n in MISSING)


def glossary(names: list[str]) -> list[str]:
    """One line per name a skill wants and this agent does not have.

    Said out loud in the prompt rather than left as an absence, because a skill's instructions read
    as though the tool exists. An agent told "there is no delegate_task here" adapts or reports;
    an agent left to discover it calls a tool that is not there and tries again.
    """
    return [f"- {name}: not available here (it would {MISSING[name]})" for name in missing_names(names)]
