"""The tools the registry holds only sometimes — and what turns each one on.

`default_registry` registers some tools unconditionally and others behind a condition: a setting that
is off by default (``edit_batch``, ``decide``), a credential (``web_search`` needs a Tavily key), a
package (``browser`` needs Playwright). The Tools screen listed only what was registered, so a tool
behind a condition was invisible exactly when it was off — the screen could switch a tool OFF and never
ON, and "what could my agent do" had no answer for any of these.

This catalogue is the answer: one row per conditional tool, naming the kind of condition and the
variables involved. Only a ``setting`` row can be switched from the screen — writing ``"1"`` to its
variable is the whole of turning it on. A ``key`` row needs a credential nobody can invent for the
user, and a ``package`` row needs an install; the screen names what is missing instead of offering a
switch that could not work. `tests/test_the_tools_screen_can_turn_a_tool_on.py` holds the catalogue
equal to the conditional registrations in `builtin.py`, so a new one cannot be added to the registry
and forgotten here.
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass
from typing import Literal

Kind = Literal["setting", "key", "package"]


@dataclass(frozen=True)
class ConditionalTool:
    name: str
    kind: Kind
    variables: tuple[str, ...]
    """``setting``: the one variable that turns it on. ``key``: the credential(s) it needs (any one of
    them suffices where the registry accepts alternatives). ``package``: empty."""
    cls: str
    """``module:Class`` — read for the description, never instantiated here."""
    requires: str = ""
    """For ``package``: the extra or package to install."""
    default_on: bool = False
    """A ``setting`` that is on unless the owner switched it off (``todo_write``)."""

    @property
    def switchable(self) -> bool:
        return self.kind == "setting"

    def description(self) -> str:
        module, _, attr = self.cls.partition(":")
        return str(getattr(getattr(importlib.import_module(module), attr), "description", ""))


CONDITIONAL_TOOLS: tuple[ConditionalTool, ...] = (
    ConditionalTool("edit_batch", "setting", ("CHIMERA_EDIT_BATCH",), "chimera.tools.edit:EditBatchTool"),
    ConditionalTool("todo_write", "setting", ("CHIMERA_TODO_LIST",), "chimera.tools.todo:TodoWriteTool", default_on=True),
    ConditionalTool("decide", "setting", ("CHIMERA_DECIDE_TOOL",), "chimera.tools.decide:DecideTool"),
    ConditionalTool(
        "create_document", "setting", ("CHIMERA_CREATE_DOCUMENT",),
        "chimera.tools.create_document:CreateDocumentTool",
    ),
    ConditionalTool("web_search", "key", ("TAVILY_API_KEY",), "chimera.tools.web:WebSearchTool"),
    ConditionalTool("generate_image", "key", ("OPENAI_API_KEY",), "chimera.tools.media:ImageGenTool", requires="diffusers"),
    ConditionalTool("text_to_speech", "key", ("ELEVENLABS_API_KEY",), "chimera.tools.media:TextToSpeechTool"),
    ConditionalTool("transcribe_audio", "key", ("OPENAI_API_KEY",), "chimera.tools.media:TranscribeAudioTool", requires="faster-whisper (the stt extra)"),
    ConditionalTool("send_email", "key", ("CHIMERA_SMTP_HOST", "CHIMERA_SMTP_USER", "CHIMERA_SMTP_PASSWORD"), "chimera.tools.email:SendEmailTool"),
    ConditionalTool("read_email", "key", ("CHIMERA_IMAP_HOST", "CHIMERA_IMAP_USER", "CHIMERA_IMAP_PASSWORD"), "chimera.tools.email:ReadEmailTool"),
    ConditionalTool("calendar_events", "key", ("CHIMERA_CALENDAR_ICS_URL",), "chimera.tools.calendar:CalendarEventsTool"),
    ConditionalTool("browser", "package", (), "chimera.tools.browser:BrowserTool", requires="playwright"),
    # Every call asks the owner whatever this switch says (`tools/pull_request.py`); the switch only
    # decides whether the agent may propose one. Owner-only: the desktop bridge cannot write it.
    ConditionalTool(
        "open_pull_request", "setting", ("CHIMERA_PULL_REQUESTS",),
        "chimera.tools.pull_request:OpenPullRequestTool",
    ),
)
"""``generate_image`` and ``transcribe_audio`` light up with a key OR a local package; they are listed as
``key`` rows with the package named in ``requires``, because the key is the path most people take."""

SWITCHABLE_SETTINGS: frozenset[str] = frozenset(
    v for t in CONDITIONAL_TOOLS if t.switchable for v in t.variables
)
"""The variables the Tools screen may write — the config allowlist includes exactly these."""


def unavailable(registered: set[str]) -> list[dict[str, object]]:
    """One row per conditional tool that is NOT in the registry right now, with what would turn it on.

    ``in_settings`` answers where the variables are set, and it is computed from the config endpoint's
    own allowlist rather than declared here. The screen used to tell every ``key`` row "add it in
    Settings", and for ``send_email``, ``read_email`` and ``calendar_events`` that was false: the SMTP,
    IMAP and ICS variables are not in ``ALLOWED_KEYS``, so Settings has no field for them and a PATCH
    would be refused. Keeping them out is deliberate — saving SMTP credentials from the app is what
    arms ``send_email``, and that is the owner's decision, not a side effect of a copy fix — so the row
    has to name the ``.env`` instead.
    """
    # Imported here, not at the top: `config_api` imports SWITCHABLE_SETTINGS from this module.
    from chimera.api.config_api import is_editable

    rows: list[dict[str, object]] = []
    for tool in CONDITIONAL_TOOLS:
        if tool.name in registered:
            continue
        rows.append({
            "name": tool.name, "description": tool.description(), "kind": tool.kind,
            "variables": list(tool.variables), "requires": tool.requires, "switchable": tool.switchable,
            "default_on": tool.default_on,
            "in_settings": bool(tool.variables) and all(is_editable(v) for v in tool.variables),
        })
    return rows
