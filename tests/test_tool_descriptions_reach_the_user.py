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
from chimera.tools.builtin import default_registry

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
    assert not (described - real), f"described but not registered: {sorted(described - real)}"
