"""The Capabilities screen answers "what may my agent do" — in the reader's language.

A tool's ``description`` is its SCHEMA: the exact sentence the model is shown when it decides
whether to call something. It is English at the source and has to stay that way — translating it
there would ship a different agent to every locale. The desktop app therefore keeps a translation of
its own, keyed by tool name, and falls back to the schema text when it has none (which is the normal
case for MCP tools, whose descriptions come from someone else's server).

That split has one failure mode, and it is silent: the schema changes in Python, nobody touches
``i18n.tsx``, and the screen keeps confidently describing behaviour the tool no longer has. Nothing
would break; the sentence would just quietly become false. So the English dictionary is pinned to
the registry here, character for character. When this fails the fix is to update the translations —
starting with ``en``, which must match the new schema exactly — not to loosen the assertion.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from chimera.api.tools_api import list_tools
from chimera.tools.builtin import OPTIONAL_TOOLS, default_registry

I18N = Path(__file__).resolve().parents[1] / "apps" / "desktop" / "src" / "lib" / "i18n.tsx"


def _english_dict() -> dict[str, str]:
    """The `en` dictionary from i18n.tsx, as {key: value}."""
    source = I18N.read_text(encoding="utf-8")
    start = source.index("const en: Dict = {")
    end = source.index("const pt: Dict = {")
    block = source[start:end]

    # Scanned as `"key": "value"` pairs across the whole block, NOT line by line: Prettier puts a
    # long value on its own line, and a line-based reader silently loses exactly the long entries —
    # which is every tool description worth checking. This was written line-based first and passed
    # over nineteen of the twenty-two.
    pair = re.compile(r'"((?:[^"\\]|\\.)*)"\s*:\s*("(?:[^"\\]|\\.)*")', re.S)
    return {m.group(1): json.loads(m.group(2)) for m in pair.finditer(block)}


def test_the_english_dictionary_was_actually_read() -> None:
    # Every assertion below is drawn from this parse. If the file is restructured and the parse
    # comes back empty, the real tests would pass over nothing at all.
    keys = _english_dict()
    assert len(keys) > 100, f"only parsed {len(keys)} keys — has i18n.tsx been restructured?"
    # The long, wrapped entries are the ones a line-based parse loses, so prove one of them is here.
    assert keys.get("tools.desc.browser", "").startswith("Navigate and read the web")
    assert "tools.title" in keys


def test_every_native_tool_has_a_translatable_description() -> None:
    keys = _english_dict()
    tools = list_tools(default_registry())
    assert tools, "the registry came back empty"

    missing = [t["name"] for t in tools if f"tools.desc.{t['name']}" not in keys]
    assert not missing, (
        "these tools would render in English on every localised screen — add "
        f"tools.desc.<name> to all ten dictionaries in i18n.tsx: {missing}"
    )


def test_the_english_text_is_the_schema_itself_word_for_word() -> None:
    """The pin. `en` is not a translation — it is the schema, copied."""
    keys = _english_dict()
    drifted = []
    for tool in list_tools(default_registry()):
        shown = keys.get(f"tools.desc.{tool['name']}")
        if shown is not None and shown != tool["description"]:
            drifted.append(tool["name"])
    assert not drifted, (
        "the schema changed and the screen still shows the old sentence, in every language: "
        f"{drifted}"
    )


def test_no_dictionary_describes_a_tool_that_does_not_exist() -> None:
    """The other direction: a tool removed from the registry leaves its description behind, and the
    screen would offer an explanation for something the agent cannot do."""
    source = I18N.read_text(encoding="utf-8")
    described = {m.group(1) for m in re.finditer(r'"tools\.desc\.([a-z0-9_]+)"', source)}
    real = {t["name"] for t in list_tools(default_registry())}
    # Minus the ones that are only sometimes here. This guard and the one above are exact mirrors,
    # so without this a conditional tool fails one of them on every machine whose credentials or
    # installed skills differ from CI's — which is also why none of the key-gated tools had a
    # description at all: describing one was impossible without turning this red.
    stray = described - real - OPTIONAL_TOOLS
    assert not stray, f"described but not registered: {sorted(stray)}"


def test_the_exemption_list_still_names_tools_that_exist() -> None:
    """`OPTIONAL_TOOLS` weakens the mirror guard above, so it has to stay honest.

    Its failure mode is quiet: a tool is deleted, its description stays in ten dictionaries, and the
    exemption keeps the guard green forever — which is precisely the silent-false-sentence problem
    this file was written to stop, reintroduced through the door built to let conditional tools in.
    """
    root = Path(__file__).resolve().parents[1] / "chimera"
    sources = "\n".join(
        path.read_text(encoding="utf-8")
        for path in [*(root / "tools").rglob("*.py"), root / "skills" / "aliases.py"]
    )

    orphans = [name for name in sorted(OPTIONAL_TOOLS) if f'"{name}"' not in sources]

    assert not orphans, (
        f"these are exempted from the registry check but nothing defines them any more: {orphans}. "
        "Remove them from OPTIONAL_TOOLS and delete their tools.desc.* entries."
    )


def test_the_optional_tools_english_is_pinned_too() -> None:
    """The word-for-word check above only sees REGISTERED tools, which is the wrong half here.

    A key-gated tool is absent on this machine, so a typo in its English description — or a schema
    edited in Python with the dictionary left behind — sails past every other assertion in this
    file and surfaces for the first user who sets that credential. Which is the same blind spot
    `OPTIONAL_TOOLS` was introduced to close for PRESENCE, reappearing for CONTENT.

    So these are read off the classes rather than off the registry: a description is a fact about
    the tool, not about whether this machine happens to have its API key.
    """
    from chimera.tools.calendar import CalendarEventsTool
    from chimera.tools.email import ReadEmailTool, SendEmailTool
    from chimera.tools.media import ImageGenTool, TextToSpeechTool, TranscribeAudioTool
    from chimera.tools.web import WebSearchTool

    keyed = [
        WebSearchTool,
        ImageGenTool,
        TextToSpeechTool,
        TranscribeAudioTool,
        SendEmailTool,
        ReadEmailTool,
        CalendarEventsTool,
    ]
    keys = _english_dict()

    drifted = [
        (cls.name, cls.description, keys.get(f"tools.desc.{cls.name}"))
        for cls in keyed
        if keys.get(f"tools.desc.{cls.name}") != cls.description
    ]

    assert not drifted, (
        "the English text must be the schema itself — update `en` first, then the other nine: "
        + "; ".join(f"{name}: schema={schema!r} shown={shown!r}" for name, schema, shown in drifted)
    )
    # And every one of them is exempted from the registry check, or the pinning above is the only
    # thing holding them and the other guard would go red the moment a credential is set.
    assert {cls.name for cls in keyed} <= OPTIONAL_TOOLS
