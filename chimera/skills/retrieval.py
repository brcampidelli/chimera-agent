"""Minimal skill-context retrieval (the seed of Tier-1 RAG).

A keyword scorer over skill name + description, used to surface the few most
relevant skills for a task and inject them as context. Vector retrieval and a
richer knowledge store arrive with the memory layers (M3/M4); this keeps the
dependency surface zero for now.
"""

from __future__ import annotations

import re

from chimera.skills.base import Skill
from chimera.skills.registry import SkillRegistry

#: A word is a run of letters/digits in ANY script. It used to be ``[a-z0-9]+``, which cut every
#: accented word into fragments ("análise" -> "an", "lise"), and a fragment is a match nobody meant.
_TOKEN = re.compile(r"[^\W_]+")

# Grammatical glue that would otherwise match every skill's description and surface irrelevant skills
# for any task (e.g. "the"/"of" in a geography question). Filtered from both sides before scoring so
# a match means a shared *content* word. Short tokens (<3 chars) are dropped too — same purpose.
_STOPWORDS = frozenset(
    {
        "the", "and", "for", "with", "that", "this", "these", "those", "you", "your", "our", "she",
        "him", "her", "his", "its", "are", "was", "were", "been", "being", "have", "has", "had",
        "can", "could", "would", "should", "will", "shall", "may", "might", "must", "does", "did",
        "what", "which", "who", "whom", "how", "when", "where", "why", "into", "from", "then",
        "than", "them", "they", "their", "there", "here", "some", "any", "all", "each", "please",
        "give", "get", "make", "want", "need", "use", "using", "about", "back", "now", "not",
    }
)


#: Words the HARNESS writes around a task, never the person. ``AutonomousAgent._compose`` heads
#: every autonomous prompt with ``Task:`` (and ``Plan:`` / ``Feedback from the previous attempt
#: (address this):`` on a plan or a retry), and that whole string is what reaches retrieval.
#: ``data_analysis`` describes itself as "a data task", so one shared word was enough: every cron
#: job, ``solve`` and ``/api/runs`` run got the data-analysis skill whatever it was about, 26 of 26
#: realistic tasks measured (study 28, P7). Pinned to ``_compose`` by a test, so a new header word
#: fails there instead of quietly matching.
_HARNESS_WORDS = frozenset({"task", "plan", "feedback", "previous", "attempt", "address"})

#: Instruction verbs inside the skill descriptions themselves ("WRITE runnable Python ... RUN the
#: RESULT with the code sandbox"). They say how a skill is used, not what it is about, and they
#: matched "Write a haiku" and "Run the tests and report the result" to both data skills. Read off
#: the same 26-task set the fix was measured on, so that set does not validate this list.
_HOW_TO_WORDS = frozenset({"write", "run", "result", "results"})

_IGNORED = _STOPWORDS | _HARNESS_WORDS | _HOW_TO_WORDS

#: Shared content words a skill needs before it counts as relevant. One was the old bar, and one
#: word is a coincidence more often than a topic: "text" put `echo` on "Translate this text", and
#: the Portuguese "data" (a date) put both data skills on "Qual a data de hoje?". A real request
#: names its topic more than once ("load the dataset, train a classifier": load, dataset, train).
MIN_SHARED_WORDS = 2


def _tokenize(text: str) -> set[str]:
    return {tok for tok in _TOKEN.findall(text.lower()) if len(tok) >= 3 and tok not in _IGNORED}


def retrieve_relevant_skills(
    registry: SkillRegistry, query: str, *, k: int = 3
) -> list[Skill]:
    """Up to ``k`` skills sharing at least :data:`MIN_SHARED_WORDS` content words with ``query``.

    The descriptions are English and nothing here translates, so a Portuguese request matches only
    where it happens to use two of the same words, which is usually never. That costs real hits:
    "Analise o dataset vendas.csv..." and "Corrija o bug no parser" each reached the right skill
    through one loanword, and no longer do. Matching across languages needs translation or
    embeddings, not a lower bar, since one shared word is also what matched "Task:".

    ``query`` is whatever the caller hands in. The autonomous runner hands in the composed prompt,
    recalled lessons included, so words from that context still count: with a two-lesson block in
    front, 2 of the 26 measured tasks picked up a wrong ``data_visualization`` (study 28, P7).
    """
    terms = _tokenize(query)
    if not terms:
        return []
    scored: list[tuple[int, str, Skill]] = []
    for skill in registry.skills():
        haystack = _tokenize(f"{skill.name} {skill.description}")
        score = len(terms & haystack)
        if score >= MIN_SHARED_WORDS:
            scored.append((score, skill.name, skill))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [skill for _, _, skill in scored[:k]]


#: The header of the skills block. It used to read "Relevant skills you can use:", above names such
#: as `fix_code` that the agent loop has no way to call: built-in skills run only inside the evolver
#: and the holdout. Told it could use them, a model either invented a call to a tool that does not
#: exist or described the skill instead of acting. What the block really is, is reference: a
#: description of a technique that fits the task (study 25, defect 8).
SKILLS_HEADER = (
    "Techniques that match this task (reference only: they describe an approach, they are not "
    "tools you can call):"
)


def skills_context_block(skills: list[Skill]) -> str:
    """Format skills as a context block to inject into a prompt."""
    if not skills:
        return ""
    lines = [SKILLS_HEADER]
    lines += [f"- {skill.name}: {skill.description}" for skill in skills]
    return "\n".join(lines)
