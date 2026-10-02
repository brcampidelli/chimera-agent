"""Explorer census — how often stored agent traces sit in the regime where reading in a sub-agent pays.

Offline and free: it reads traces already committed under ``bench/`` and calls nothing. The rules it
applies (what counts as exploration, the counterfactual, the recommendation) are frozen in
``PREREGISTRATION.md``; this file is that text as arithmetic, committed with it before it ran.

The mechanism, from ``bench/hierarchy_multistep`` and ``bench/hierarchy_sweep``: an agent loop
re-sends every tool result on every later call, so what it read while LOCATING code is paid again on
each call it spends SOLVING. An explorer sub-agent pays for the reading once, inside its own loop, and
hands the main loop a short ``path:line`` block instead. The census estimates, per stored solve, what
the main loop would have paid had its opening read-only phase gone through the explorer.

Run: ``python bench/explorer_census/census.py`` (writes ``results/census.json`` and prints the report).
"""

from __future__ import annotations

import json
import re
import statistics
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent

PRIMARY = ROOT / "bench" / "default_model" / "results" / "main_solves.jsonl"
SECONDARY_DIRECTIVE = ROOT / "bench" / "directive_boundary" / "results" / "run.json"
SECONDARY_UNATTENDED = ROOT / "bench" / "unattended_claims" / "results" / "pilot_fixtures.jsonl"

#: Tools that only look. A call whose every tool is one of these belongs to the exploration phase.
READ_TOOLS = frozenset({"read_file", "grep", "glob", "list_dir"})
#: Tools that neither read the repository nor change it. They do not end exploration: an agent that
#: writes its todo list between two searches is still searching.
NEUTRAL_TOOLS = frozenset({"todo_write"})

#: Tokens of the main loop's own ``explore_repository`` call message (the query it writes).
EXPLORE_CALL_TOKENS = 60
#: The returned block, two readings. 300 is the plain contract's eight ``path:line (note)`` lines;
#: 1000 is the S12 contract's findings plus gaps, with room to spare. The decision reads 1000.
BLOCKS = (300, 1000)
CONSERVATIVE_BLOCK = 1000

#: USD per million tokens (input, cache read), each arm's pinned endpoint, as frozen in
#: ``bench/default_model/PREREGISTRATION.md`` (read from OpenRouter on 2026-09-26).
PRICES: dict[str, tuple[float, float]] = {
    "A": (0.06, 0.015),
    "D": (0.14, 0.0042),
    "G": (0.10, 0.01),
    "Q": (0.03, 0.006),
}

#: The regime the hierarchy benches measured: several documents read, then several turns that carry
#: them. D=3 and Q=3 are bench/hierarchy_multistep's own setting.
REGIME_MIN_READS = 3
REGIME_MIN_POST_CALLS = 3

_DIFF_FILE = re.compile(r"^diff --git a/(\S+) b/", re.MULTILINE)


@dataclass(frozen=True)
class Call:
    prompt: int
    cache_read: int
    tools: tuple[str, ...]


@dataclass(frozen=True)
class Trace:
    arm: str
    item: str
    calls: tuple[Call, ...]
    patch_files: int


@dataclass(frozen=True)
class Phase:
    """The opening read-only phase of a trace: ``e`` calls, holding ``reads`` read_file calls."""

    e: int
    reads: int
    searches: int
    post_calls: int


def split_calls(
    calls: Sequence[dict[str, Any]], tool_names: Sequence[str]
) -> tuple[Call, ...] | None:
    """Give each recorded model call the tools it emitted, in order. None when the counts disagree.

    The trace stores the tool names as one flat list and each call's tool count separately, so the
    only honest join is to walk the list by the counts; a trace whose counts do not sum to the list
    cannot say which call read what, and is left out rather than guessed."""
    if sum(int(c.get("tool_calls", 0)) for c in calls) != len(tool_names):
        return None
    out: list[Call] = []
    k = 0
    for c in calls:
        n = int(c.get("tool_calls", 0))
        out.append(
            Call(int(c["prompt"]), int(c.get("cache_read") or 0), tuple(tool_names[k : k + n]))
        )
        k += n
    return tuple(out)


def exploration_phase(calls: Sequence[Call]) -> Phase:
    """The longest opening run of calls that only looked.

    A call that emitted no tool is the model talking between steps and does not end the phase,
    unless it is the last call, which is the answer and therefore solving."""
    e = 0
    last = len(calls) - 1
    for i, c in enumerate(calls):
        if not c.tools:
            if i == last:
                break
            e = i + 1
            continue
        if all(t in READ_TOOLS or t in NEUTRAL_TOOLS for t in c.tools):
            e = i + 1
            continue
        break
    head = [t for c in calls[:e] for t in c.tools]
    reads = sum(1 for t in head if t == "read_file")
    searches = sum(1 for t in head if t in READ_TOOLS and t != "read_file")
    return Phase(e=e, reads=reads, searches=searches, post_calls=len(calls) - e)


def append_only(calls: Sequence[Call]) -> bool:
    """True when no call's prompt is smaller than the one before it.

    The counterfactual subtracts what was read from every later prompt, which is only right if the
    loop kept everything it read. A loop that dropped or compacted context breaks that, and its trace
    is left out."""
    return all(b.prompt >= a.prompt for a, b in zip(calls, calls[1:], strict=False))


def single_read_sizes(calls: Sequence[Call]) -> list[int]:
    """Prompt growth after each call that emitted exactly one ``read_file``: one read's size, plus
    the assistant message that asked for it (which overstates the read, the conservative side)."""
    return [
        calls[i + 1].prompt - calls[i].prompt
        for i in range(len(calls) - 1)
        if calls[i].tools == ("read_file",)
    ]


def patch_file_count(patch: str) -> int:
    return len(set(_DIFF_FILE.findall(patch or "")))


@dataclass(frozen=True)
class Counterfactual:
    base_tokens: int
    cf_tokens: int
    base_usd: float
    cf_usd: float

    @property
    def saving(self) -> float:
        return 1 - self.cf_tokens / self.base_tokens if self.base_tokens else 0.0

    @property
    def usd_saving(self) -> float:
        return 1 - self.cf_usd / self.base_usd if self.base_usd else 0.0


def _usd(prompt: int, cache: int, price: tuple[float, float]) -> float:
    cache = max(0, min(cache, prompt))
    return ((prompt - cache) * price[0] + cache * price[1]) / 1_000_000


def counterfactual(
    trace: Trace, phase: Phase, *, block: int, reread: bool, read_size: float
) -> Counterfactual:
    """Prompt tokens (and input dollars) of the trace as it ran, and as it would have run with its
    opening read-only phase delegated to an explorer.

    The explorer arm, call by call (PREREGISTRATION.md, "The counterfactual"):

    * the main loop's first call, which asks the explorer instead of reading: same size as call 0;
    * the explorer re-does the exploration calls at the SAME size as the main loop paid for them,
      although its system prompt and tool list are smaller (conservative);
    * the explorer's closing call sees everything it read: the size of the main loop's call ``e``;
    * every later main call carries the block instead of what was read: ``prompt_i - R + X + B + RR``,
      where ``R`` is what the exploration added, ``X`` the explore call, ``B`` the block and ``RR``
      the files the main loop must open again to edit them;
    * the re-reads are extra calls of the same size as the first post-explorer call.

    Completion tokens are left out of both arms: they are the same work in both, apart from the
    explorer's block, which is counted on the prompt side of every call that carries it."""
    calls = trace.calls
    price = PRICES.get(trace.arm, (1.0, 0.1))
    base_tokens = sum(c.prompt for c in calls)
    base_usd = sum(_usd(c.prompt, c.cache_read, price) for c in calls)
    if phase.e == 0 or phase.post_calls == 0 or phase.reads + phase.searches == 0:
        return Counterfactual(base_tokens, base_tokens, base_usd, base_usd)

    e = phase.e
    r = calls[e].prompt - calls[0].prompt
    rereads = min(phase.reads, max(1, trace.patch_files)) if reread else 0
    rr = round(rereads * read_size)
    # Positive: tokens the explorer takes out of each later prompt. Negative: it costs more than it
    # read, which happens when the reads were small and the block is not.
    removed = r - EXPLORE_CALL_TOKENS - block - rr

    def shrunk(c: Call) -> tuple[int, float]:
        p = c.prompt - removed
        # What leaves a later prompt is its OLDEST content, so it leaves the cached part; tokens the
        # explorer adds are charged as uncached.
        cache = c.cache_read - removed if removed > 0 else c.cache_read
        return p, _usd(p, cache, price)

    cf_tokens = calls[0].prompt + sum(c.prompt for c in calls[:e]) + calls[e].prompt
    cf_usd = (
        _usd(calls[0].prompt, calls[0].cache_read, price)
        + sum(_usd(c.prompt, c.cache_read, price) for c in calls[:e])
        + _usd(calls[e].prompt, calls[e].cache_read, price)
    )
    for c in calls[e:]:
        p, u = shrunk(c)
        cf_tokens += p
        cf_usd += u
    p_first, u_first = shrunk(calls[e])
    cf_tokens += rereads * p_first
    cf_usd += rereads * u_first
    return Counterfactual(base_tokens, cf_tokens, base_usd, cf_usd)


def in_regime(phase: Phase) -> bool:
    return phase.reads >= REGIME_MIN_READS and phase.post_calls >= REGIME_MIN_POST_CALLS


# --- loading -------------------------------------------------------------------------------------


@dataclass
class Loaded:
    traces: list[Trace]
    rows: int
    halted: int
    misaligned: int
    no_calls: int
    not_append_only: int


def load_primary(path: Path = PRIMARY) -> Loaded:
    traces: list[Trace] = []
    rows = halted = misaligned = no_calls = not_append = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        rows += 1
        if row.get("halted") is not None:
            halted += 1  # PROTOCOL §2: a halt leaves every denominator.
            continue
        if not row.get("calls"):
            no_calls += 1
            continue
        calls = split_calls(row["calls"], row.get("tool_names") or [])
        if calls is None:
            misaligned += 1
            continue
        if not append_only(calls):
            not_append += 1
            continue
        traces.append(
            Trace(
                str(row["arm"]),
                str(row["instance_id"]),
                calls,
                patch_file_count(row.get("patch", "")),
            )
        )
    return Loaded(traces, rows, halted, misaligned, no_calls, not_append)


def secondary_sequences() -> dict[str, list[list[str]]]:
    """Tool sequences without per-call tokens: the regime's share can be read, the saving cannot."""
    out: dict[str, list[list[str]]] = {"directive_boundary": [], "unattended_claims": []}
    data = json.loads(SECONDARY_DIRECTIVE.read_text(encoding="utf-8"))
    for row in data["rows"]:
        for replicas in row["runs"].values():
            for run in replicas:
                if run.get("error") is None:
                    out["directive_boundary"].append(list(run.get("tools") or []))
    for line in SECONDARY_UNATTENDED.read_text(encoding="utf-8").splitlines():
        if line.strip():
            for run in json.loads(line)["runs"].values():
                if run.get("error") is None:
                    out["unattended_claims"].append(list(run.get("tools") or []))
    return out


def sequence_reads(tools: Sequence[str]) -> int:
    """read_file calls before the first tool that is neither a read nor neutral."""
    n = 0
    for t in tools:
        if t == "read_file":
            n += 1
        elif t not in READ_TOOLS and t not in NEUTRAL_TOOLS:
            break
    return n


# --- report --------------------------------------------------------------------------------------


def histogram(values: Iterable[int], edges: Sequence[int]) -> dict[str, int]:
    """Counts in buckets ``[edges[i], edges[i+1])``, the last one open."""
    vals = list(values)
    out: dict[str, int] = {}
    for i, lo in enumerate(edges):
        hi = edges[i + 1] if i + 1 < len(edges) else None
        label = f"{lo}+" if hi is None else (str(lo) if hi == lo + 1 else f"{lo}-{hi - 1}")
        out[label] = sum(1 for v in vals if v >= lo and (hi is None or v < hi))
    return out


def _median(xs: Sequence[float]) -> float | None:
    return statistics.median(xs) if xs else None


def analyse(loaded: Loaded) -> dict[str, Any]:
    traces = loaded.traces
    sizes_by_arm: dict[str, list[int]] = {}
    for t in traces:
        sizes_by_arm.setdefault(t.arm, []).extend(single_read_sizes(t.calls))
    pooled_size = {a: (_median(v) or 0.0) for a, v in sizes_by_arm.items()}

    per: list[dict[str, Any]] = []
    for t in traces:
        ph = exploration_phase(t.calls)
        own = single_read_sizes(t.calls)
        size = float(statistics.median(own)) if own else pooled_size.get(t.arm, 0.0)
        cfs = {
            f"B{b}_{'reread' if rr else 'noreread'}": counterfactual(
                t, ph, block=b, reread=rr, read_size=size
            )
            for b in BLOCKS
            for rr in (True, False)
        }
        per.append({"trace": t, "phase": ph, "cf": cfs, "read_size": size})

    key = f"B{CONSERVATIVE_BLOCK}_reread"

    def summary(sel: list[dict[str, Any]]) -> dict[str, Any]:
        if not sel:
            return {"n": 0}
        out: dict[str, Any] = {"n": len(sel)}
        for k in per[0]["cf"]:
            base = sum(p["cf"][k].base_tokens for p in sel)
            cf = sum(p["cf"][k].cf_tokens for p in sel)
            bu = sum(p["cf"][k].base_usd for p in sel)
            cu = sum(p["cf"][k].cf_usd for p in sel)
            out[k] = {
                "pooled_token_saving": round(1 - cf / base, 4) if base else None,
                "median_token_saving": round(_median([p["cf"][k].saving for p in sel]) or 0.0, 4),
                "pooled_usd_saving": round(1 - cu / bu, 4) if bu else None,
                "negative_rows": sum(1 for p in sel if p["cf"][k].saving < 0),
            }
        return out

    regime = [p for p in per if in_regime(p["phase"])]
    arms = sorted({p["trace"].arm for p in per})
    cache_share = {
        a: round(
            sum(c.cache_read for p in per if p["trace"].arm == a for c in p["trace"].calls)
            / max(1, sum(c.prompt for p in per if p["trace"].arm == a for c in p["trace"].calls)),
            4,
        )
        for a in arms
    }
    law = [(p["phase"].reads - 1) / p["phase"].reads for p in regime]
    sec = secondary_sequences()

    report: dict[str, Any] = {
        "source": str(PRIMARY.relative_to(ROOT)).replace("\\", "/"),
        "rows": loaded.rows,
        "excluded": {
            "halted": loaded.halted,
            "no_calls": loaded.no_calls,
            "misaligned": loaded.misaligned,
            "not_append_only": loaded.not_append_only,
        },
        "usable": len(per),
        "regime_rule": f"reads >= {REGIME_MIN_READS} in the opening read-only phase and >= "
        f"{REGIME_MIN_POST_CALLS} calls after it",
        "regime_n": len(regime),
        "regime_share": round(len(regime) / len(per), 4) if per else None,
        "regime_share_by_arm": {
            a: round(
                sum(1 for p in regime if p["trace"].arm == a)
                / max(1, sum(1 for p in per if p["trace"].arm == a)),
                4,
            )
            for a in arms
        },
        "histogram_reads_in_phase": histogram(
            (p["phase"].reads for p in per), [0, 1, 2, 3, 4, 6, 10]
        ),
        "histogram_post_calls": histogram(
            (p["phase"].post_calls for p in per), [0, 1, 3, 10, 20, 30]
        ),
        "median_reads_in_phase": _median([p["phase"].reads for p in per]),
        "median_phase_calls": _median([p["phase"].e for p in per]),
        "median_read_size_tokens": _median([p["read_size"] for p in per]),
        "all": summary(per),
        "regime": summary(regime),
        "regime_by_arm": {a: summary([p for p in regime if p["trace"].arm == a]) for a in arms},
        "all_by_arm": {a: summary([p for p in per if p["trace"].arm == a]) for a in arms},
        "cache_read_share_of_prompt": cache_share,
        "law_reference_median_(D-1)/D": round(_median(law) or 0.0, 4),
        "secondary": {
            name: {
                "runs": len(seqs),
                "share_reads_ge_3": round(
                    sum(1 for s in seqs if sequence_reads(s) >= REGIME_MIN_READS) / len(seqs), 4
                )
                if seqs
                else None,
                "histogram_reads_before_first_action": histogram(
                    (sequence_reads(s) for s in seqs), [0, 1, 2, 3, 4, 6]
                ),
            }
            for name, seqs in sec.items()
        },
        "decision_key": key,
    }
    report["verdict"] = verdict(report)
    return report


def verdict(r: dict[str, Any]) -> dict[str, Any]:
    """The frozen rule of PREREGISTRATION.md, "The recommendation rule"."""
    key = r["decision_key"]
    usable = r["usable"]
    checked = usable + r["excluded"]["misaligned"] + r["excluded"]["not_append_only"]
    failed = r["excluded"]["misaligned"] + r["excluded"]["not_append_only"]
    if usable < 100 or (checked and failed / checked > 0.25):
        return {"verdict": "VOID", "why": f"usable={usable}, failed checks={failed}/{checked}"}
    share = r["regime_share"] or 0.0
    med = (r["regime"].get(key) or {}).get("median_token_saving") or 0.0
    pooled = (r["all"].get(key) or {}).get("pooled_token_saving") or 0.0
    conds = {
        "regime_share >= 0.25": share >= 0.25,
        "median saving in regime >= 0.20": med >= 0.20,
        "pooled saving over all >= 0.10": pooled >= 0.10,
    }
    v = "RECOMMEND" if all(conds.values()) else "DO NOT RECOMMEND"
    usd = (r["all"].get(key) or {}).get("pooled_usd_saving") or 0.0
    labels = ["proxy population: unattended SWE-bench solves, not attended Code turns"]
    if usd < 0.05:
        labels.append("token-only: the cache-weighted input-dollar saving is under 5%")
    return {"verdict": v, "conditions": conds, "labels": labels}


def render(r: dict[str, Any]) -> str:
    return json.dumps(r, indent=2, ensure_ascii=False)


def main(argv: Sequence[str] = ()) -> int:
    loaded = load_primary()
    report = analyse(loaded)
    out = HERE / "results"
    out.mkdir(exist_ok=True)
    (out / "census.json").write_text(render(report) + "\n", encoding="utf-8")
    print(render(report))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))


__all__ = [
    "Call",
    "Phase",
    "Trace",
    "counterfactual",
    "exploration_phase",
    "in_regime",
    "patch_file_count",
    "sequence_reads",
    "split_calls",
]
