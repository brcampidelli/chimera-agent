"""The three arms of H4/H5, frozen in PREREGISTRATION.md before any paid call.

    python bench/prompt_overlays/arms.py      # prints A -> B as a sentence diff and the CAPS audit

- **A** is ``chimera.core.agent.DEFAULT_SYSTEM_PROMPT`` byte for byte, at the loop's 0.2.
- **B** is A's instructions with the CAPS emphasis removed and each rule carrying its reason in a
  few words (H4). Same rules, none added or dropped; written for this bench, no vendor text.
- **C** is A's text at the vendor's recommended sampling for this model (H5): DeepSeek's model card
  for DeepSeek-V4-Flash-0731 recommends temperature 1.0, with top_p 0.95 for agentic use.

The fence sentence (``UNTRUSTED_DATA_RULE``) closes B unchanged, "DATA" included. It is not ours to
reword here: ``Agent.compose_system_prompt`` appends the constant verbatim to any system prompt that
does not contain it, so a de-capitalised copy would be sent *next to* the original, not instead of it.
Keeping it byte-identical is the only way B sends exactly one fence sentence without touching product
code, and it is identical in all three arms.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from chimera.core.agent import DEFAULT_SYSTEM_PROMPT, UNTRUSTED_DATA_RULE  # noqa: E402

#: The sha256 of DEFAULT_SYSTEM_PROMPT this bench was registered against. A different default means
#: arm A is no longer the prompt B was written from, and the run refuses to start.
REGISTERED_A_SHA256 = "66299259ecf8e09b5072f5fb7abea3b93927b35d13b620ae68b1413ea19a1810"
REGISTERED_B_SHA256 = "f9523908fb4493db910c0e696f212bbbedd928d38fe9fc322dbafdc042a058a8"

#: Arm B's own paragraph, sentence by sentence (the fence sentence follows it, unchanged).
B_SENTENCES: tuple[str, ...] = (
    "You are Chimera, a capable autonomous agent.",
    "Your job is to do the task, not to describe how to do it, because the result is what was asked for.",
    "Use the provided tools to actually carry it out — run the commands, make the edits, create the files.",
    "Investigating or explaining the solution is not enough: if you know what to do, do it with the tools "
    "before you finish, because an explanation leaves the change unmade.",
    "A final answer that only tells the user what they 'can' or 'should' do is a failure, because it hands "
    "the work back to them.",
    "Give a concise final answer only after the change has actually been made, then stop calling tools, so "
    "the answer reports what was done.",
    "One exception, and it is deliberately narrow: when the request does not contain enough to begin — no "
    "technology, no audience, and nowhere for the result to live — ask the few questions that actually "
    "block you, at most three, and stop without writing anything, because files built on a guess are files "
    "nobody asked for.",
    "Only when a guess would produce the wrong thing rather than merely a different one.",
    "Someone asking for a site for their bakery is better served by a question than by a framework they "
    "cannot host.",
    "If the request names what to build and where, do not ask — build it, because then a question only "
    "delays the work.",
    "To change an existing file, prefer edit_file (or apply_patch for several edits) over write_file — edit "
    "in place instead of rewriting the whole file, so the lines you did not mean to change stay as they were.",
)

SYSTEM_A = DEFAULT_SYSTEM_PROMPT
# Same assembly as the default: sentences joined by one space, a trailing space, then the fence.
SYSTEM_B = " ".join(B_SENTENCES) + " " + UNTRUSTED_DATA_RULE


def assert_frozen() -> None:
    """Refuse to run if either arm's bytes differ from the registered ones."""
    import hashlib

    for label, text, want in (("A", SYSTEM_A, REGISTERED_A_SHA256), ("B", SYSTEM_B, REGISTERED_B_SHA256)):
        got = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if got != want:
            raise SystemExit(f"arm {label} changed since registration ({got[:12]} != {want[:12]}); re-register")

MODEL = "openrouter/deepseek/deepseek-v4-flash-0731"
PROVIDER = "DeepInfra"


@dataclass(frozen=True)
class Arm:
    name: str
    system: str
    temperature: float
    top_p: float | None  # None: not sent, the endpoint's default applies (as in production)


ARMS: dict[str, Arm] = {
    "A": Arm("A", SYSTEM_A, 0.2, None),
    "B": Arm("B", SYSTEM_B, 0.2, None),
    "C": Arm("C", SYSTEM_A, 1.0, 0.95),
}

#: Words that are emphasis in capitals. Tool names and quoted identifiers are lower-case, so any
#: all-caps word of two or more letters counts.
_CAPS = re.compile(r"\b[A-Z]{2,}\b")


def caps_words(text: str) -> list[str]:
    return _CAPS.findall(text)


def _split_sentences(text: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s]


def check() -> None:
    """Print the A -> B diff sentence by sentence, the CAPS audit and the word counts."""
    import difflib
    import hashlib

    head_a = SYSTEM_A[: SYSTEM_A.index(UNTRUSTED_DATA_RULE)]
    sa, sb = _split_sentences(head_a), list(B_SENTENCES)
    for line in difflib.unified_diff(sa, sb, "A (DEFAULT_SYSTEM_PROMPT)", "B", lineterm="", n=0):
        print(line)
    print(f"\nfence sentence identical and last in both: {SYSTEM_A.endswith(UNTRUSTED_DATA_RULE)} "
          f"{SYSTEM_B.endswith(UNTRUSTED_DATA_RULE)}")
    print(f"CAPS words  A: {caps_words(SYSTEM_A)}  B: {caps_words(SYSTEM_B)}")
    print(f"words       A: {len(SYSTEM_A.split())}  B: {len(SYSTEM_B.split())}")
    print(f"sha256      A: {hashlib.sha256(SYSTEM_A.encode()).hexdigest()}")
    print(f"sha256      B: {hashlib.sha256(SYSTEM_B.encode()).hexdigest()}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    check()
