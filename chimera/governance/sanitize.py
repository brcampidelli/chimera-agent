"""Neutralize chat-template / control tokens smuggled inside untrusted content (M15-A3).

OpenClaw's post-crisis hardening wraps external content in explicit markers AND strips the
chat-template tokens a page or document could embed to fake a system/user turn or a tool call
(``<|im_start|>``, ``[INST]``, ``<tool_call>`` ...). Chimera already fences fetched content
(M9-A2, :func:`chimera.governance.ledger_tool.fence`); this adds the token-stripping half on the way
in, plus a matching outbound pass so a model that parroted such a token from tainted content cannot
leak a live control marker to whatever renders the answer.

Known-imperfect mitigation, not a boundary — the sandbox and taint escalation remain the real
containment. It only removes the cheapest structural-spoofing trick. The replacement is a *visible*
placeholder, never a silent deletion, so nothing changes length-invisibly and an auditor can see
that a control token was present.
"""

from __future__ import annotations

import re

# The chat-template / control-token families a page or document might embed to break out of the
# data fence: ChatML specials (``<|...|>``), Llama/Mistral system + instruction markers, sentence
# boundaries, and fake tool/function-call tags.
_CONTROL_TOKEN_RE = re.compile(
    r"<\|[^|>\n]{0,40}\|>"  # <|im_start|>, <|im_end|>, <|system|>, <|endoftext|>, <|tool|> ...
    r"|<</?SYS>>"  # <<SYS>> <</SYS>>
    r"|\[/?INST\]"  # [INST] [/INST]
    r"|</?s>"  # <s> </s>
    r"|</?(?:tool_call|function_call|tool_response)>",  # fake tool-call structure
    re.IGNORECASE,
)
_PLACEHOLDER = "⟦stripped⟧"  # ⟦stripped⟧ — visible, so nothing is deleted silently


def sanitize_untrusted(content: str) -> str:
    """Defang chat-template/control tokens embedded in untrusted content, on the way *in*."""
    return _CONTROL_TOKEN_RE.sub(_PLACEHOLDER, content)


def strip_leaked_control_tokens(text: str) -> str:
    """Remove control tokens the model may have echoed from tainted content, on the way *out*."""
    return _CONTROL_TOKEN_RE.sub(_PLACEHOLDER, text)


def has_control_tokens(text: str) -> bool:
    """True if ``text`` contains any recognized chat-template / control token."""
    return _CONTROL_TOKEN_RE.search(text) is not None
