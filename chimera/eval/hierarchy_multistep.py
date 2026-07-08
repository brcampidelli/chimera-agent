"""Multi-step hierarchy A/B (M16 companion) — where the token crossover actually lives.

The single-shot suite (:mod:`chimera.eval.hierarchy_ab`) showed the hierarchy CANNOT
win on tokens: one baseline call carries all docs once, and fanning out only adds
per-worker + synthesis overhead. That is honest and expected.

This companion isolates the regime the literature actually credits — **multi-step**
work over **large** documents:

- **baseline (single context):** a sequential assistant answers Q sub-questions in
  ONE growing conversation. Every turn re-sends the whole context, so all documents
  are paid for again on every turn -> cost grows like ``Q * sum(docs)``.
- **scoped (hierarchy):** each sub-question is routed to a worker that sees ONLY the
  one document it needs -> each document is paid for ~once, cost like ``sum(docs)``.

With large docs and Q >= 3 the baseline's re-send dominates and the scoped arm uses
materially fewer tokens. Grading stays deterministic (planted needles, ALL must
appear across the answers). Quality is paired McNemar/Wilson; tokens are measured
totals only.

Honesty footnote carried into the report: token COUNTS are real, but a provider with
prompt caching bills the baseline's repeated prefix at ~0.1x, so the DOLLAR gap is
narrower than the token gap. We measure tokens, never claim cost significance, and
say this out loud.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

# complete(messages) -> (text, tokens): the single seam both arms call. Tokens is the
# measured (prompt+completion) count for that one call; the arms sum it across calls.
Complete = Callable[[list[dict[str, str]]], tuple[str, int]]


@dataclass(frozen=True)
class Step:
    """One sub-question, the document it concerns, and the needle a correct answer holds."""

    question: str
    doc: str  # key into MultiStepTask.docs
    needle: str


@dataclass(frozen=True)
class MultiStepTask:
    """Q sequential sub-questions over k large documents, deterministically gradable."""

    id: str
    docs: dict[str, str]
    steps: tuple[Step, ...]

    def check(self, answers: list[str]) -> bool:
        blob = "\n".join(answers).lower()
        return all(s.needle.lower() in blob for s in self.steps)


@dataclass
class ArmRun:
    """One arm over one task: did every needle land, and how many tokens did it cost."""

    passed: bool
    tokens: int


_SYS_BASE = (
    "You are a precise research assistant. Answer each question using the documents "
    "in this conversation. Quote exact figures and names verbatim."
)
_SYS_WORKER = (
    "You are a precise extraction worker. Using ONLY the single document below, answer "
    "the question. Quote exact figures and names verbatim. Do not speculate."
)


def _docs_block(docs: dict[str, str]) -> str:
    return "\n\n".join(f"### {name}\n{content}" for name, content in docs.items())


def run_baseline(task: MultiStepTask, complete: Complete) -> ArmRun:
    """Single growing context: all docs enter on turn 1 and ride along every later turn."""
    messages: list[dict[str, str]] = [
        {"role": "system", "content": _SYS_BASE},
        {"role": "user", "content": f"Here are the documents:\n\n{_docs_block(task.docs)}"},
        {"role": "assistant", "content": "Understood. Ask your questions."},
    ]
    answers: list[str] = []
    total = 0
    for step in task.steps:
        messages.append({"role": "user", "content": step.question})
        text, tokens = complete(messages)
        total += tokens
        messages.append({"role": "assistant", "content": text})
        answers.append(text)
    return ArmRun(passed=task.check(answers), tokens=total)


def run_scoped(task: MultiStepTask, complete: Complete) -> ArmRun:
    """Hierarchy: each sub-question sees ONLY its own document — no cross-doc carry."""
    answers: list[str] = []
    total = 0
    for step in task.steps:
        doc = task.docs[step.doc]
        messages = [
            {"role": "system", "content": _SYS_WORKER},
            {"role": "user", "content": f"### {step.doc}\n{doc}\n\nQuestion: {step.question}"},
        ]
        text, tokens = complete(messages)
        total += tokens
        answers.append(text)
    return ArmRun(passed=task.check(answers), tokens=total)


# --- deterministic large-doc corpus (no randomness; replay-safe) ---------------------

_FILLER_UNIT = (
    "Background. This section records process notes, historical context, tooling "
    "decisions and review commentary that a careful reader must skip past to find the "
    "operative facts. It is deliberately verbose and repetitive so that the document "
    "carries real weight when it is re-sent on every conversational turn.\n"
)
#: Default doc size (~5-6k chars): big enough that re-sending it every turn dominates.
_DEFAULT_FILLER_REPS = 40


def _big_doc(title: str, facts: list[str], filler_reps: int = _DEFAULT_FILLER_REPS) -> str:
    body = "\n".join(f"- {fact}" for fact in facts)
    filler = _FILLER_UNIT * filler_reps
    return f"# {title}\n\n{filler}\n## Key items\n{body}\n\n{filler}"


def _needle(fact: str) -> str:
    words = fact.split()
    for i, word in enumerate(words):
        if any(ch.isdigit() for ch in word):
            return " ".join(words[i : i + 2]).rstrip(".,")
    return " ".join(words[-3:]).rstrip(".,")


def multistep_tasks() -> list[MultiStepTask]:
    """6 tasks, 3 large docs each, one sub-question per doc (Q=3): the crossover regime."""
    raw: list[tuple[str, dict[str, list[str]], list[tuple[str, str]]]] = [
        (
            "regions",
            {
                "eu.md": ["EU region has 320 spare cores"],
                "us.md": ["US region has 75 spare cores"],
                "apac.md": ["APAC region has 140 spare cores"],
            },
            [("How many spare cores does the EU region have?", "eu.md"),
             ("How many spare cores does the US region have?", "us.md"),
             ("How many spare cores does the APAC region have?", "apac.md")],
        ),
        (
            "vendors",
            {
                "acme.md": ["Acme charges 14 dollars per seat"],
                "globex.md": ["Globex charges 11 dollars per seat"],
                "initech.md": ["Initech charges 19 dollars per seat"],
            },
            [("What is Acme's per-seat price?", "acme.md"),
             ("What is Globex's per-seat price?", "globex.md"),
             ("What is Initech's per-seat price?", "initech.md")],
        ),
        (
            "policies",
            {
                "security.md": ["Security policy mandates rotation every 90 days"],
                "privacy.md": ["Privacy policy mandates deletion within 30 days"],
                "access.md": ["Access policy mandates review every 180 days"],
            },
            [("What interval does the security policy mandate?", "security.md"),
             ("What interval does the privacy policy mandate?", "privacy.md"),
             ("What interval does the access policy mandate?", "access.md")],
        ),
        (
            "contracts",
            {
                "north.md": ["North contract renews on March 15"],
                "south.md": ["South contract renews on August 3"],
                "east.md": ["East contract renews on November 20"],
            },
            [("When does the North contract renew?", "north.md"),
             ("When does the South contract renew?", "south.md"),
             ("When does the East contract renew?", "east.md")],
        ),
        (
            "limits",
            {
                "api.md": ["The API rate limit is 600 requests per minute"],
                "batch.md": ["The batch job ceiling is 250 concurrent jobs"],
                "store.md": ["The object store caps objects at 5 gigabytes"],
            },
            [("What is the API rate limit?", "api.md"),
             ("What is the batch job ceiling?", "batch.md"),
             ("What is the object size cap?", "store.md")],
        ),
        (
            "teams",
            {
                "core.md": ["Core team owns the scheduler module"],
                "infra.md": ["Infra team owns the deployment pipeline"],
                "data.md": ["Data team owns the metrics warehouse"],
            },
            [("What does the Core team own?", "core.md"),
             ("What does the Infra team own?", "infra.md"),
             ("What does the Data team own?", "data.md")],
        ),
    ]
    tasks: list[MultiStepTask] = []
    for task_id, docs, steps in raw:
        rendered = {name: _big_doc(name, facts) for name, facts in docs.items()}
        step_objs = tuple(
            Step(question=q, doc=doc, needle=_needle(docs[doc][0])) for q, doc in steps
        )
        tasks.append(MultiStepTask(id=task_id, docs=rendered, steps=step_objs))
    return tasks


# Digit-free doc names so _needle locks onto the FACT's number, not the filename.
_PHONETIC = ("alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "golf", "hotel")


def make_sweep_task(
    num_docs: int,
    *,
    filler_reps: int = _DEFAULT_FILLER_REPS,
    num_steps: int | None = None,
) -> MultiStepTask:
    """A task parameterized on THREE independent crossover axes:

    - **D** (``num_docs``): how many docs a single agent must juggle. The isolation
      win scales like (D-1)/D — every turn re-sends all D docs; workers read one each.
    - **S** (``filler_reps``): document size. At tiny S the fixed per-call framing
      (system prompts, questions) is a bigger slice, shrinking the win; at large S the
      doc dominates and the win approaches (D-1)/D. The win rises with S but does not
      invert here — the true loss regime is the single-shot bench (fan-out + synthesis).
    - **Q** (``num_steps``): conversation length. Sub-questions cycle over the docs to
      reach Q turns (default Q = D). Both arms scale ~linearly in Q, so the win is
      roughly flat in Q — a stability check, not a lever.
    """
    if not 1 <= num_docs <= len(_PHONETIC):
        raise ValueError(f"num_docs must be in 1..{len(_PHONETIC)}")
    if filler_reps < 1:
        raise ValueError("filler_reps must be >= 1")
    docs: dict[str, str] = {}
    base_steps: list[Step] = []
    for i in range(num_docs):
        name = f"{_PHONETIC[i]}.md"
        fact = f"The {_PHONETIC[i]} report records {100 + i * 7} units"
        docs[name] = _big_doc(name, [fact], filler_reps)
        base_steps.append(Step(question=f"What does {name} record?", doc=name, needle=_needle(fact)))
    q = num_steps if num_steps is not None else num_docs
    if q < 1:
        raise ValueError("num_steps must be >= 1")
    steps = tuple(base_steps[i % num_docs] for i in range(q))
    return MultiStepTask(id=f"sweep-d{num_docs}-r{filler_reps}-q{q}", docs=docs, steps=steps)


def make_hetero_task(sizes: list[int]) -> MultiStepTask:
    """Robustness variant: D docs of DIFFERENT sizes (one sub-question per doc).

    The isolation law is size-distribution-agnostic for one-question-per-doc: a single
    agent re-sends ALL docs every turn (Σsizes × D turns) while scoped workers each read
    one doc (Σsizes total), so the saving stays ≈ (D-1)/D no matter how unequal the docs
    are. This generator lets a test prove that the win doesn't depend on uniform docs.
    """
    if not 1 <= len(sizes) <= len(_PHONETIC):
        raise ValueError(f"need 1..{len(_PHONETIC)} sizes")
    docs: dict[str, str] = {}
    steps: list[Step] = []
    for i, reps in enumerate(sizes):
        if reps < 1:
            raise ValueError("each size must be >= 1")
        name = f"{_PHONETIC[i]}.md"
        fact = f"The {_PHONETIC[i]} report records {100 + i * 7} units"
        docs[name] = _big_doc(name, [fact], reps)
        steps.append(Step(question=f"What does {name} record?", doc=name, needle=_needle(fact)))
    return MultiStepTask(id=f"hetero-{'-'.join(map(str, sizes))}", docs=docs, steps=tuple(steps))
