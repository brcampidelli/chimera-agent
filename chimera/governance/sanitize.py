"""Neutralize chat-template / control tokens smuggled inside untrusted content (M15-A3).

OpenClaw's post-crisis hardening wraps external content in explicit markers AND strips the
chat-template tokens a page or document could embed to fake a system/user turn or a tool call
(``<|im_start|>``, ``[INST]``, ``<tool_call>`` ...). "The chat-template tokens" means the families
listed in ``_CONTROL_TOKEN_RE`` and no others; the list is enumerated by hand from the tokenizers in
use, so a model added later is covered only once its specials are added there. Chimera already fences fetched content
(M9-A2, :func:`chimera.governance.ledger_tool.fence`); this adds the token-stripping half on the way
in, plus a matching outbound pass so a model that parroted such a token from tainted content cannot
leak a live control marker to whatever renders the answer.

Known-imperfect mitigation, not a boundary — the sandbox and taint escalation remain the real
containment. It only removes the cheapest structural-spoofing trick. The replacement is a *visible*
placeholder, never a silent deletion, so nothing changes length-invisibly and an auditor can see
that a control token was present.

**The false positive is real, and it lands on code.** The inbound pass runs with no gate on every
attached text file (:mod:`chimera.api.attachments`), every research fetch and every recalled memory
fact, and it cannot tell a token used as a token from a token quoted as a string. A Python file that
parses reasoning output — ``answer.split("</think>")`` — reaches the model as
``answer.split("⟦stripped⟧")``, while the bytes on disk are unchanged, so the model is asked to
reason about code that is not what the file says. ``<s>``, ``<tool_call>`` and ChatML already had
this cost; ``</think>`` is the most common of them in LLM-application code, which is why it is said
here. Pinned by ``tests/test_sanitize.py``; exempting source files would hand the same fake turn a
``.py`` suffix, so the trade-off is kept and stated rather than removed.
"""

from __future__ import annotations

import re

# The chat-template / control-token families a page or document might embed to break out of the
# data fence: ChatML specials (``<|...|>``), Llama/Mistral system + instruction markers, sentence
# boundaries, and fake tool/function-call tags.
#
# The list once stopped there, while the docstring promised the chat-template tokens in general. Three
# families were missing, and they are the ones that matter here (study 30, S30-21(d)):
# - reasoning markers ``<think>`` / ``</think>`` — with tool markers, the tokens that most often
#   survive a standard mitigation in an audit of 256 chat tokenizers (arXiv 2609.16984);
# - DeepSeek's specials, written with FULL-WIDTH bars (U+FF5C) and U+2581 for spaces
#   (``<｜User｜>``, ``<｜begin▁of▁sentence｜>``), which the ASCII ``<|...|>`` pattern can never match;
#   deepseek-chat is the default model on the production VPS;
# - Gemma's ``<start_of_turn>`` / ``<end_of_turn>``; gemma-4-12B is the chosen local model.
# Still a list, so still not a boundary: a tokenizer added later brings its own specials.
_CONTROL_TOKEN_RE = re.compile(
    r"<\|[^|>\n]{0,40}\|>"  # <|im_start|>, <|im_end|>, <|system|>, <|endoftext|>, <|tool|> ...
    r"|<｜[^｜>\n]{0,40}｜>"  # DeepSeek full-width: <｜User｜>, <｜Assistant｜>, <｜end▁of▁sentence｜>
    r"|<</?SYS>>"  # <<SYS>> <</SYS>>
    r"|\[/?INST\]"  # [INST] [/INST]
    r"|</?s>"  # <s> </s>
    r"|</?(?:tool_call|function_call|tool_response)>"  # fake tool-call structure
    r"|</?think>"  # reasoning markers
    r"|<(?:start|end)_of_turn>",  # Gemma turn markers
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
