"""Phrases in a tool description that try to steer which tool the model picks.

Study 30, S30-24, second half. Assertive cues in a description ("always use this tool", "prefer this
over any other") were measured to move a tool's selection share by 7-11x (arXiv 2505.18135), and the
poisoned-description attacks in MCPTox (2508.14925) are written in the same register. A description
is supposed to say what a tool does; one that tells the model what to do instead is worth a second
look before the server is added.

**An annotation, never a gate.** Plenty of honest servers write "IMPORTANT:" in a description, and a
regular expression is the wrong instrument to refuse anything on. What this returns is shown next to
the tool on the MCP screen and in ``chimera mcp test``; it decides nothing. No measurement has said
how often it fires on benign servers, so it carries no claim beyond "this phrase is here".

The cues are returned as CODES, not sentences: the app ships in ten languages and translates them.
"""

from __future__ import annotations

import re

#: code -> patterns. Matched case-insensitively against the description.
_CUES: dict[str, tuple[str, ...]] = {
    # Telling the model to pick this tool, rather than saying what the tool does.
    "imperative": (
        r"\balways\s+(?:use|call|invoke|run|prefer)\b",
        r"\byou\s+must\s+(?:use|call|invoke|run|first)\b",
        r"\bmust\s+(?:always\s+)?be\s+(?:used|called)\b",
        r"\b(?:use|call|invoke|run)\s+this\s+(?:tool|function)\s+(?:first|before|instead|whenever|for\s+every|for\s+all)\b",
        r"\bbefore\s+(?:using|calling)\s+any\s+other\s+tool\b",
    ),
    # Pushing the other tools out.
    "exclusivity": (
        r"\b(?:do\s+not|don't|never)\s+(?:use|call)\s+(?:any\s+)?other\s+tools?\b",
        r"\binstead\s+of\s+(?:any|all|the)\s+other\s+tools?\b",
        r"\bprefer\s+this\s+(?:tool|function)\b",
        r"\bthe\s+only\s+tool\s+(?:you\s+)?(?:should|need)\b",
        r"\bbetter\s+than\s+(?:any|all|the)\s+other\s+tools?\b",
    ),
    # Addressing the model's instructions themselves.
    "override": (
        r"\b(?:ignore|disregard|forget)\s+(?:all\s+|any\s+)?(?:previous|prior|above|earlier|other)\s+instructions\b",
        r"\bsystem\s+prompt\b",
        r"\bdo\s+not\s+(?:tell|inform|mention\s+(?:this\s+)?to)\s+the\s+user\b",
    ),
    # Shouting, in the tag form the poisoning papers use and the plain one.
    "emphasis": (
        r"<\s*important\s*>",
        r"\b(?:IMPORTANT|CRITICAL|MANDATORY)\s*[:!]",
    ),
}

_COMPILED: dict[str, tuple[re.Pattern[str], ...]] = {
    code: tuple(re.compile(p, re.IGNORECASE) for p in patterns) for code, patterns in _CUES.items()
}
# "IMPORTANT:" is a cue in capitals; "important:" in a sentence is ordinary prose.
_COMPILED["emphasis"] = (
    re.compile(_CUES["emphasis"][0], re.IGNORECASE),
    re.compile(_CUES["emphasis"][1]),
)

#: The codes in the order they are reported, so the output is stable.
CUE_CODES: tuple[str, ...] = tuple(_CUES)


def selection_cues(description: str) -> list[str]:
    """The cue codes present in ``description``, in :data:`CUE_CODES` order; ``[]`` when none."""
    text = description or ""
    return [code for code in CUE_CODES if any(p.search(text) for p in _COMPILED[code])]
