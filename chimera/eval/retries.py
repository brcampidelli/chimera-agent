"""Retry contamination — are the attempts of one run independent draws? A ruler over run receipts.

The solve loop (`chimera/core/autonomous.py`) hands a failed attempt generic feedback and tries
again, up to ``max_attempts``. Budgeting that loop as if each attempt were a fresh coin at the same
rate — pass@k = 1 − (1 − p)^k — is the IID model, and arXiv 2605.08563 measured it failing on
coding agents: attempts share the task, the context and the failure, and the IID model overestimated
pass@3 by 17.4 pp. Whether that holds *here* is a question this install's own ``runs.jsonl`` answers
for free, because every receipt carries one ``success`` per attempt and what each attempt cost.

Three readings, each with its ``n`` beside it and ``None`` wherever the data cannot support a number:

1. **The attempt-conditional table.** For attempt ``k``: how many runs reached it with every earlier
   attempt failed, and how many of those succeeded. Beside each row, the IID prediction from the
   previous row's rate and the exact binomial lower tail P(observed or fewer | independence),
   computed by hand with :func:`math.comb`. The tail is a one-sided check in the direction the
   paper predicts (retries recover *less* than independence says); a tail near 0.5 is the probe
   saying "no effect", which it must be able to say (§2s).
2. **pass@k against 1 − (1 − p1)^k** — the paper's own comparison — over the runs the receipt
   shows were allowed ``k`` attempts, bracketed by the number under the opposite assumption.
3. **The money per attempt index**, as a share of everything in the file.

What a receipt does *not* carry, handled rather than guessed:

- ``max_attempts`` is not persisted (:class:`chimera.api.runs.RunReceipt` has no such field), so a
  run's eligibility for pass@k is *inferred*: a run that passed within ``k`` attempts or that has at
  least ``k`` attempts was allowed ``k``; a run that failed and stopped earlier with no ceiling was
  exhausted at a smaller cap and is excluded; a run the ceiling stopped earlier is counted apart.
  The exclusion biases observed pass@k *upward* — against the contamination reading — so the report
  also prints the floor under the opposite assumption, and the two bracket the truth.
- A run stopped by a ceiling (``stopped_reason`` ``spend`` / ``budget`` / ``cancelled``, or the
  same ``ending``) records its last attempt as a *partial* one that never reached a verdict
  (``_partial_attempt`` in the loop). Such an attempt is marked ``cut`` and reported beside the
  judged ones, never silently as a failure the model earned.
- Receipts are read raw, not through :func:`chimera.api.runs.load_runs`: that loader's Pydantic
  defaults turn an absent ``success`` into ``False`` and an absent ``attempts`` into ``[]`` — the
  app's honest defaults for display, and exactly the silent wrong number a probe must never print.
  Here a row missing what the probe needs is *unreadable*, counted and excluded.

Distinct tasks are counted beside every ``n`` (§2p: "600 cases" were 87 problems), by hashing the
task text in memory; the text itself is never carried out of the parser.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "CAP_REASONS",
    "AttemptOutcome",
    "ConditionalRow",
    "MoneyByIndex",
    "PassAtK",
    "ReceiptFile",
    "RunOutcome",
    "binomial_tail",
    "conditional_rows",
    "flags_contamination",
    "format_report",
    "max_rate_consistent",
    "money_by_index",
    "parse_receipt",
    "pass_at_k",
    "read_receipts",
    "zero_successes_needed",
]

#: The loop endings that record a partial last attempt — one that was stopped, not judged.
CAP_REASONS: frozenset[str] = frozenset({"spend", "budget", "cancelled"})


@dataclass(frozen=True)
class AttemptOutcome:
    """One attempt as the receipt recorded it: its verdict, whether a ceiling cut it, and its cost."""

    index: int
    success: bool
    #: The run's ceiling fired during this attempt, so it never reached a verdict. Derived from the
    #: run (a capped run's LAST attempt is the partial one), not from the attempt's own booleans.
    cut: bool
    usd: float | None
    tool_calls: int | None
    prompt_tokens: int | None
    completion_tokens: int | None


@dataclass(frozen=True)
class RunOutcome:
    """One run: its attempts in index order, the cap it recorded (if any), and a task fingerprint."""

    attempts: tuple[AttemptOutcome, ...]
    #: ``max_attempts`` as persisted on the receipt, or ``None`` — no receipt written so far has it.
    max_attempts: int | None
    #: SHA-256 of the task text, for counting distinct tasks. Never the text.
    task_key: str
    stopped_reason: str

    @property
    def passed_within(self) -> int | None:
        """The attempt index that succeeded, or ``None`` when no attempt did."""
        for a in self.attempts:
            if a.success:
                return a.index
        return None

    @property
    def capped(self) -> bool:
        return self.stopped_reason in CAP_REASONS


@dataclass(frozen=True)
class ReceiptFile:
    """What a ``runs.jsonl`` yielded: the readable runs, and how many rows were not."""

    runs: tuple[RunOutcome, ...]
    #: JSON objects that lack a readable attempts list (missing or non-boolean ``success``, absent
    #: or non-contiguous ``index``). Excluded, never defaulted.
    unreadable: int
    #: Lines that are not a JSON object at all.
    malformed: int


def _as_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _as_float(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def parse_receipt(record: Mapping[str, object]) -> RunOutcome | None:
    """Read one receipt into a :class:`RunOutcome`, or ``None`` when it cannot be read honestly.

    ``None``, not a run with defaults: an attempt whose ``success`` is missing has no verdict, and
    counting it as a failure would manufacture the very effect this ruler exists to measure.
    """
    raw_attempts = record.get("attempts")
    if not isinstance(raw_attempts, list):
        return None
    reason = str(record.get("stopped_reason") or "")
    ending = str(record.get("ending") or "")
    stopped = reason if reason in CAP_REASONS else (ending if ending in CAP_REASONS else reason)
    capped = stopped in CAP_REASONS
    parsed: list[AttemptOutcome] = []
    for position, item in enumerate(raw_attempts):
        if not isinstance(item, Mapping):
            return None
        success = item.get("success")
        index = _as_int(item.get("index"))
        if not isinstance(success, bool) or index is None or index < 1:
            return None
        tools = item.get("tool_names")
        parsed.append(
            AttemptOutcome(
                index=index,
                success=success,
                cut=capped and position == len(raw_attempts) - 1,
                usd=_as_float(item.get("usd")),
                tool_calls=len(tools) if isinstance(tools, list) else None,
                prompt_tokens=_as_int(item.get("prompt_tokens")),
                completion_tokens=_as_int(item.get("completion_tokens")),
            )
        )
    parsed.sort(key=lambda a: a.index)
    if [a.index for a in parsed] != list(range(1, len(parsed) + 1)):
        return None
    task = record.get("task")
    key = hashlib.sha256(str(task if task is not None else "").encode("utf-8")).hexdigest()
    return RunOutcome(
        attempts=tuple(parsed),
        max_attempts=_as_int(record.get("max_attempts")),
        task_key=key,
        stopped_reason=stopped,
    )


def read_receipts(path: Path) -> ReceiptFile:
    """Load a ``runs.jsonl`` line by line, counting what could not be read instead of skipping it."""
    runs: list[RunOutcome] = []
    unreadable = malformed = 0
    with Path(path).open(encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                malformed += 1
                continue
            if not isinstance(record, dict):
                malformed += 1
                continue
            run = parse_receipt(record)
            if run is None:
                unreadable += 1
            else:
                runs.append(run)
    return ReceiptFile(runs=tuple(runs), unreadable=unreadable, malformed=malformed)


# --- the statistics ---------------------------------------------------------------------------


def binomial_tail(at_most: int, n: int, p: float) -> float | None:
    """P(X ≤ ``at_most``) for X ~ Binomial(``n``, ``p``), exactly, term by term with :func:`math.comb`.

    ``None`` for an empty trial (``n ≤ 0``), a negative count, or a rate outside [0, 1]: there is no
    tail to compute, and 0.0 would read as "impossible under independence".
    """
    if n <= 0 or at_most < 0 or not 0.0 <= p <= 1.0:
        return None
    top = min(at_most, n)
    return sum(math.comb(n, i) * p**i * (1.0 - p) ** (n - i) for i in range(top + 1))


def zero_successes_needed(p: float, alpha: float = 0.05) -> int | None:
    """The smallest ``n`` at which *zero* successes would fall below ``alpha`` under rate ``p``.

    The power statement for a row that observed nothing: how many trials a null result needs before
    it can be read. ``None`` when ``p`` is 0 or 1 (nothing is ever, or always, recovered) or
    ``alpha`` is not a probability.
    """
    if not 0.0 < p < 1.0 or not 0.0 < alpha < 1.0:
        return None
    return math.ceil(math.log(alpha) / math.log(1.0 - p))


def max_rate_consistent(at_most: int, n: int, alpha: float = 0.05) -> float | None:
    """The largest rate ``p`` at which observing ``at_most`` or fewer of ``n`` still has tail ≥ ``alpha``.

    The counter-question for a flagged row: how low would the previous row's true rate have to be
    for this count to be ordinary under independence? If the receipt cannot rule that rate out
    (it cannot separate launch paths, for instance), the flag is a statement about the pooled rate,
    not about the retry. Found by bisection on :func:`binomial_tail`, which is monotone in ``p``.
    ``None`` when there is no tail to read.
    """
    if n <= 0 or at_most < 0 or not 0.0 < alpha < 1.0:
        return None
    if at_most >= n:
        return 1.0
    lo, hi = 0.0, 1.0
    for _ in range(60):
        mid = (lo + hi) / 2
        tail = binomial_tail(at_most, n, mid)
        assert tail is not None
        if tail >= alpha:
            lo = mid
        else:
            hi = mid
    return lo


def flags_contamination(tail: float | None, alpha: float = 0.05) -> bool | None:
    """Whether a row's lower tail is below ``alpha``; ``None`` when there was no tail to read."""
    if tail is None:
        return None
    return tail < alpha


@dataclass(frozen=True)
class ConditionalRow:
    """Attempt ``index`` given every earlier attempt failed, with the IID prediction beside it."""

    index: int
    n: int
    succeeded: int
    failed: int
    #: Of the failed, attempts a ceiling cut before any verdict.
    cut: int
    distinct_tasks: int
    #: Distinct tasks among the successes — four recoveries of one task are one recovery (§2p).
    distinct_tasks_succeeded: int
    #: The previous row's observed rate, and its n. ``None`` on the first row.
    p_prev: float | None
    n_prev: int
    #: ``n × p_prev``: successes independence predicts for this row.
    expected: float | None
    #: P(X ≤ succeeded | n, p_prev): the exact lower tail under independence.
    tail: float | None
    #: The same tail with the cut attempts removed from ``n`` and from the previous row's rate.
    tail_judged: float | None
    #: Only when nothing succeeded: the ``n`` at which zero would reach ``alpha`` under ``p_prev``.
    zero_needs: int | None
    #: The largest previous-row rate at which this count would still be ordinary (tail ≥ alpha).
    p_prev_max_consistent: float | None

    @property
    def rate(self) -> float | None:
        return self.succeeded / self.n if self.n else None

    @property
    def rate_judged(self) -> float | None:
        judged = self.n - self.cut
        return self.succeeded / judged if judged else None


def conditional_rows(
    runs: Sequence[RunOutcome], *, alpha: float = 0.05
) -> tuple[ConditionalRow, ...]:
    """The attempt-conditional table, one row per attempt index the receipts reached.

    Row 1 is every run with at least one attempt. Row ``k`` is every run whose attempts 1..k−1 all
    failed and that has an attempt ``k`` — checked, not assumed from the loop: a receipt that had a
    later attempt after a success would be excluded here, not folded in.
    """
    deepest = max((len(r.attempts) for r in runs), default=0)
    rows: list[ConditionalRow] = []
    prev: ConditionalRow | None = None
    for k in range(1, deepest + 1):
        members = [
            r
            for r in runs
            if len(r.attempts) >= k and all(not a.success for a in r.attempts[: k - 1])
        ]
        n = len(members)
        succeeded = sum(1 for r in members if r.attempts[k - 1].success)
        cut = sum(1 for r in members if r.attempts[k - 1].cut and not r.attempts[k - 1].success)
        p_prev = prev.rate if prev is not None else None
        p_prev_judged = prev.rate_judged if prev is not None else None
        tail = binomial_tail(succeeded, n, p_prev) if p_prev is not None else None
        tail_judged = (
            binomial_tail(succeeded, n - cut, p_prev_judged) if p_prev_judged is not None else None
        )
        rows.append(
            ConditionalRow(
                index=k,
                n=n,
                succeeded=succeeded,
                failed=n - succeeded,
                cut=cut,
                distinct_tasks=len({r.task_key for r in members}),
                distinct_tasks_succeeded=len(
                    {r.task_key for r in members if r.attempts[k - 1].success}
                ),
                p_prev=p_prev,
                n_prev=prev.n if prev is not None else 0,
                expected=n * p_prev if p_prev is not None and n else None,
                tail=tail,
                tail_judged=tail_judged,
                zero_needs=(
                    zero_successes_needed(p_prev, alpha)
                    if succeeded == 0 and p_prev is not None
                    else None
                ),
                p_prev_max_consistent=(
                    max_rate_consistent(succeeded, n, alpha) if p_prev is not None else None
                ),
            )
        )
        prev = rows[-1]
    return tuple(rows)


@dataclass(frozen=True)
class PassAtK:
    """pass@k over the runs the receipt shows were allowed ``k`` attempts, against the IID model."""

    k: int
    #: Runs allowed ``k`` attempts by a recorded cap, or — with no cap recorded — runs that passed
    #: within ``k`` or reached attempt ``k``.
    eligible: int
    passes: int
    observed: float | None
    #: 1 − (1 − p1)^k from the first-attempt rate over every run with an attempt.
    iid: float | None
    p1: float | None
    n1: int
    #: Failed runs a ceiling stopped before attempt ``k``: eligibility unknown, excluded, listed.
    cut_before_k: int
    #: Failed runs that stopped before attempt ``k`` with no ceiling: their cap was smaller.
    exhausted_before_k: int
    #: The number if every run excluded above had been allowed ``k`` and failed: passes over ``n1``.
    floor: float | None
    #: How many runs' eligibility came from a recorded ``max_attempts`` versus inference.
    recorded: int
    inferred: int


def pass_at_k(runs: Sequence[RunOutcome], k: int) -> PassAtK:
    """Observed pass@k and the IID prediction, with the selection bracket printed rather than hidden."""
    with_attempts = [r for r in runs if r.attempts]
    n1 = len(with_attempts)
    first = sum(1 for r in with_attempts if r.attempts[0].success)
    p1 = first / n1 if n1 else None
    eligible = passes = cut_before = exhausted_before = recorded = inferred = 0
    passes_any = 0
    for run in with_attempts:
        within = run.passed_within
        passed = within is not None and within <= k
        passes_any += 1 if passed else 0
        if run.max_attempts is not None:
            recorded += 1
            if run.max_attempts >= k:
                eligible += 1
                passes += 1 if passed else 0
            continue
        inferred += 1
        if passed or len(run.attempts) >= k:
            eligible += 1
            passes += 1 if passed else 0
        elif run.capped:
            cut_before += 1
        else:
            exhausted_before += 1
    return PassAtK(
        k=k,
        eligible=eligible,
        passes=passes,
        observed=passes / eligible if eligible else None,
        iid=1.0 - (1.0 - p1) ** k if p1 is not None else None,
        p1=p1,
        n1=n1,
        cut_before_k=cut_before,
        exhausted_before_k=exhausted_before,
        floor=passes_any / n1 if n1 else None,
        recorded=recorded,
        inferred=inferred,
    )


@dataclass(frozen=True)
class MoneyByIndex:
    """What each attempt index cost, as a share of everything in the file — or ``None``.

    All-or-nothing like :func:`chimera.api.runs.total_usd`: one unpriced attempt makes the total
    unknown, and a share of an unknown total is not a number.
    """

    total: float | None
    by_index: dict[int, float] | None
    share: dict[int, float] | None
    #: Spent on attempts a ceiling cut before any verdict.
    cut_usd: float | None
    unpriced_attempts: int
    runs_without_attempts: int


def money_by_index(runs: Sequence[RunOutcome]) -> MoneyByIndex:
    """Sum every attempt's ``usd`` by index; refuse to sum at all if any attempt is unpriced."""
    unpriced = sum(1 for r in runs for a in r.attempts if a.usd is None)
    empty = sum(1 for r in runs if not r.attempts)
    if unpriced:
        return MoneyByIndex(None, None, None, None, unpriced, empty)
    by_index: dict[int, float] = {}
    cut_usd = 0.0
    for run in runs:
        for a in run.attempts:
            assert a.usd is not None
            by_index[a.index] = by_index.get(a.index, 0.0) + a.usd
            if a.cut:
                cut_usd += a.usd
    total = sum(by_index.values())
    share = {i: (v / total if total else 0.0) for i, v in sorted(by_index.items())}
    return MoneyByIndex(
        total=round(total, 6),
        by_index={i: round(v, 6) for i, v in sorted(by_index.items())},
        share=share,
        cut_usd=round(cut_usd, 6),
        unpriced_attempts=0,
        runs_without_attempts=empty,
    )


# --- the report -------------------------------------------------------------------------------


def _pct(value: float | None) -> str:
    return "None" if value is None else f"{value:.1%}"


def _num(value: float | None, digits: int = 3) -> str:
    return "None" if value is None else f"{value:.{digits}f}"


def _usd(value: float | None) -> str:
    return "None" if value is None else f"US$ {value:.4f}"


def format_report(file: ReceiptFile, *, alpha: float = 0.05, max_k: int = 3) -> str:
    """Every number with its ``n``; ``None`` wherever the receipts cannot support one."""
    runs = file.runs
    with_attempts = [r for r in runs if r.attempts]
    lines = [
        f"receipts: {len(runs) + file.unreadable + file.malformed} lines — {len(runs)} readable, "
        f"{file.unreadable} unreadable (no per-attempt verdict), {file.malformed} malformed",
        f"runs with >=1 attempt: {len(with_attempts)}   without any attempt: "
        f"{len(runs) - len(with_attempts)}   distinct tasks: {len({r.task_key for r in runs})}",
        f"max_attempts recorded on the receipt: "
        f"{sum(1 for r in runs if r.max_attempts is not None)} of {len(runs)}",
        "",
        "(a)+(b) attempt k, given every earlier attempt failed — and what independence predicted",
        "  k   n  succ  fail  cut  tasks   rate     p_prev   expected   P(<=obs)  judged-only  "
        "flag",
    ]
    for row in conditional_rows(with_attempts, alpha=alpha):
        flag = flags_contamination(row.tail, alpha)
        flag_txt = "None" if flag is None else ("BELOW IID" if flag else "consistent")
        lines.append(
            f"  {row.index:<2} {row.n:>3}  {row.succeeded:>4}  {row.failed:>4}  {row.cut:>3}  "
            f"{row.distinct_tasks:>5}   {_pct(row.rate):<7}  {_num(row.p_prev):<7}  "
            f"{_num(row.expected, 2):<8}   {_num(row.tail, 4):<8}  {_num(row.tail_judged, 4):<11}  "
            f"{flag_txt}"
        )
        if row.index > 1:
            lines.append(
                f"       successes came from {row.distinct_tasks_succeeded} distinct task(s); "
                f"the row's {row.n} runs cover {row.distinct_tasks}"
            )
        if row.zero_needs is not None:
            lines.append(
                f"       zero of {row.n} cannot reach p<{alpha:g} at rate {row.p_prev:.3f}: "
                f"it would take n>={row.zero_needs} with still nothing recovered"
            )
        if flag and row.p_prev_max_consistent is not None:
            lines.append(
                f"       independence would need the previous row's true rate to be "
                f"<= {row.p_prev_max_consistent:.3f} for {row.succeeded} of {row.n} to be ordinary"
            )
    lines += [
        "  p_prev = the previous row's observed rate; expected = n x p_prev; P(<=obs) = exact "
        "binomial lower tail of the observed count under p_prev.",
        "  cut = the ceiling stopped the run during this attempt (no verdict); judged-only "
        "removes those from n and from p_prev.",
        "",
        "(c) pass@k over runs allowed k attempts, against IID 1-(1-p1)^k",
        "  k  eligible  passes  observed   iid      gap(pp)  floor    cut<k  exhausted<k  "
        "recorded/inferred",
    ]
    for k in range(1, max_k + 1):
        p = pass_at_k(with_attempts, k)
        gap = (p.iid - p.observed) * 100 if p.iid is not None and p.observed is not None else None
        lines.append(
            f"  {k}  {p.eligible:>8}  {p.passes:>6}  {_pct(p.observed):<8}  {_pct(p.iid):<7}  "
            f"{_num(gap, 1):<7}  {_pct(p.floor):<7}  {p.cut_before_k:>5}  {p.exhausted_before_k:>11}  "
            f"{p.recorded}/{p.inferred}"
        )
    p1_row = pass_at_k(with_attempts, 1)
    lines += [
        f"  p1 = {_pct(p1_row.p1)} over n1 = {p1_row.n1} first attempts. eligible = passed within k "
        "or reached attempt k (max_attempts is not on the receipt); exhausted<k = failed and stopped "
        "earlier with no ceiling, so its cap was smaller; floor = passes over every run with an "
        "attempt, i.e. if every excluded run had been allowed k and failed.",
        "",
        "(d) money by attempt index, share of every priced attempt in the file",
    ]
    money = money_by_index(runs)
    if money.total is None:
        lines.append(
            f"  total: None — {money.unpriced_attempts} attempt(s) carry no price, and a partial "
            "sum would be a number in the flattering direction"
        )
    else:
        assert money.by_index is not None and money.share is not None
        lines.append(f"  total: {_usd(money.total)} over {len(with_attempts)} priced runs")
        for index, usd in money.by_index.items():
            lines.append(
                f"  attempt #{index}: {_usd(usd)}  = {_pct(money.share[index])} of the total"
            )
        lines.append(f"  spent on attempts a ceiling cut before any verdict: {_usd(money.cut_usd)}")
    lines.append(
        f"  runs without any attempt (no money recorded for them): {money.runs_without_attempts}"
    )
    cut_attempts = [(r, a) for r in runs for a in r.attempts if a.cut]
    if cut_attempts:
        lines += ["", "cut attempts (never judged):  attempt  usd  tool_calls  prompt  completion"]
        for _run, a in cut_attempts:
            lines.append(
                f"  #{a.index}  {_usd(a.usd)}  {a.tool_calls}  {a.prompt_tokens}  "
                f"{a.completion_tokens}"
            )
    return "\n".join(lines)
