"""Does the spot-check auditor notice a critical result the summary dropped, when it is handed the
summary first? Registered in `PREREGISTRATION.md` before any model call.

Two arms over the same envelopes: `shipped` is the production `EnvelopeVerifier._spot_check`, called
through `verify(..., force_spot=True)` and unchanged; `blind` extracts findings from the raw output
without seeing the summary, then checks the summary against that list item by item. Two auditors,
three replications, one temperature.

    python bench/blind_audit/corpus.py                                  # once: the worker outputs
    python bench/blind_audit/run.py --out bench/blind_audit/results/<tag>.jsonl
    python bench/blind_audit/run.py --report bench/blind_audit/results/<tag>.jsonl
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
import time
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from bench.blind_audit.corpus import (  # noqa: E402
    POSITIONS,
    CorpusItem,
    instrument_check,
    load_corpus,
    make_envelope,
)
from chimera.config import get_settings  # noqa: E402
from chimera.eval.anytime import wilson_bounds  # noqa: E402
from chimera.eval.paired import compare_paired  # noqa: E402
from chimera.orchestration.artifacts import ArtifactStore  # noqa: E402
from chimera.orchestration.envelope_verify import (  # noqa: E402
    _SPOT_SYSTEM_DROPPED_ONLY,
    _SPOT_SYSTEM_THREE_CHECKS,
    COMPARE_SYSTEM,
    EXTRACT_SYSTEM,
    EnvelopeVerifier,
    _grade_faithfulness,
)
from chimera.orchestration.receipts import price_completion  # noqa: E402

ARMS = ("shipped", "blind", "shipped_dropped_only")

# --- the blind arm: the two prompts live in the module the product runs (`envelope_verify`), and
# they are byte-identical to the strings this bench was registered and run with — checked on
# 2026-09-11 before the swap, so the numbers in RESULTS.md are the numbers this code produces.
_EXTRACT_SYSTEM = EXTRACT_SYSTEM
_COMPARE_SYSTEM = COMPARE_SYSTEM


def _retrying(call: Any, *, tries: int = 6, wait: float = 20.0) -> Any:
    """Retry a provider call on a 429. The weak tier is served from shared upstream pools that
    overload for minutes at a time; one such minute must not end a run or bias its sample."""
    for attempt in range(tries):
        try:
            return call()
        except Exception as exc:  # noqa: BLE001 — only the rate-limit shape is retried
            text = str(exc)
            if attempt == tries - 1 or ("429" not in text and "rate-limit" not in text.lower()):
                raise
            time.sleep(wait * (attempt + 1))
    raise RuntimeError("unreachable")


class _Recording:
    """Wraps a backend so the production verifier can be called unchanged while tokens are metered.

    Retries a 429 here rather than in the verifier, because `_spot_check` swallows every exception
    into "spot check unavailable — passing through", and a rate-limited auditor would then be
    recorded as a PASS."""

    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self.results: list[Any] = []

    def complete(self, messages: Any, **kwargs: Any) -> Any:
        result = _retrying(lambda: self.inner.complete(messages, **kwargs))
        self.results.append(result)
        return result


@dataclass
class Call:
    auditor: str
    arm: str
    item_id: str
    domain: str
    position: str
    rep: int
    verdict: str
    """FAIL or PASS — what `_grade_faithfulness` (or the blind stage 2) says."""
    failed_on: list[str]
    """Which named checks said FAIL (shipped arm) or ['DROPPED'] (blind arm)."""
    reply: str
    findings: str
    critical_findings: int
    usd: float | None
    prompt_tokens: int
    completion_tokens: int
    seconds: float


def _failed_lines(reply: str) -> list[str]:
    up = reply.upper()
    return [key for key in ("INVENTED", "DROPPED", "CONTRADICT")
            if re.search(rf"{key}\w*\s*[:=-]?\s*FAIL", up)]


def _meter(results: list[Any]) -> tuple[float | None, int, int]:
    usd, ptok, ctok, unpriced = 0.0, 0, 0, False
    for r in results:
        cost = price_completion(r)
        usd += cost.usd
        unpriced = unpriced or cost.unpriced is not None
        ptok += r.prompt_tokens or 0
        ctok += r.completion_tokens or 0
    return (None if unpriced else usd), ptok, ctok


def audit_shipped(gateway: Any, store: ArtifactStore, spec: Any, envelope: Any, *, model: str,
                  spot_system: str = _SPOT_SYSTEM_THREE_CHECKS) -> tuple[str, str, list[Any]]:
    """The production path: `EnvelopeVerifier.verify(force_spot=True)`, backend metered, nothing else.

    `spot_system` is the prompt under test: the three-check one that shipped until 2026-09-11 (the
    `shipped` arm, the number in RESULTS.md) or the DROPPED-only one (`shipped_dropped_only`, the
    registered addendum). Both are the module's own strings."""
    rec = _Recording(gateway)
    verifier = EnvelopeVerifier(
        store=store, backend=rec, model=model, spot_rate=1.0, recover_dropped=False,
        spot_system=spot_system,
    )
    outcome = verifier.verify(spec, envelope, force_spot=True)
    if "spot" not in outcome.checks_run or outcome.stage != "spot":
        # `_spot_check` returns None when the auditor call fails and `verify` then ACCEPTS the
        # envelope un-spotted; that is the production behaviour and it must not be scored as a PASS.
        raise RuntimeError(f"spot check did not decide: {outcome.stage} {outcome.detail[:80]}")
    # The auditor's verdict is read off its reply, not off `passed`: with the recovery on, a
    # DROPPED verdict passes the envelope and appends what was named, and `passed` would read PASS
    # for every row. `recover_dropped=False` above keeps the two readings identical either way.
    return ("PASS" if _grade_faithfulness(outcome.detail) else "FAIL"), outcome.detail, rec.results


def audit_blind(gateway: Any, store: ArtifactStore, spec: Any, envelope: Any, *, model: str) -> tuple[str, str, str, int, list[Any]]:
    """Stage 1 never sees the summary; stage 2 never sees the raw output."""
    raw = store.get(envelope.evidence_refs[0])
    gateway = _Recording(gateway)
    stage1 = gateway.complete(
        [
            {"role": "system", "content": _EXTRACT_SYSTEM},
            {"role": "user", "content": f"## Task\n{spec.objective}\n\n## Raw output (may be truncated)\n{raw[:24_000]}"},
        ],
        model=model, temperature=0.0,
    )
    findings = (stage1.content or "").strip()
    critical = len(re.findall(r"\[CRITICAL\]", findings, re.I))
    stage2 = gateway.complete(
        [
            {"role": "system", "content": _COMPARE_SYSTEM},
            {"role": "user", "content": f"## Findings\n{findings}\n\n## Summary\n{envelope.summary}"},
        ],
        model=model, temperature=0.0,
    )
    reply = (stage2.content or "").strip()
    verdict = "PASS" if _grade_faithfulness(reply) else "FAIL"
    return verdict, reply, findings, critical, [stage1, stage2]


def one(item: CorpusItem, position: str, arm: str, rep: int, *, auditor: str, store: ArtifactStore) -> Call:
    from chimera.providers import LLMGateway

    spec, envelope, planted = make_envelope(item, position, store)
    problem = instrument_check(item, position, envelope, planted)
    if problem:
        raise RuntimeError(f"instrument: {problem}")
    gateway = LLMGateway()
    t0 = time.monotonic()
    findings, critical = "", 0
    if arm == "shipped":
        verdict, reply, results = audit_shipped(gateway, store, spec, envelope, model=auditor)
        failed_on = _failed_lines(reply)
    elif arm == "shipped_dropped_only":
        verdict, reply, results = audit_shipped(
            gateway, store, spec, envelope, model=auditor, spot_system=_SPOT_SYSTEM_DROPPED_ONLY,
        )
        failed_on = _failed_lines(reply)
    else:
        verdict, reply, findings, critical, results = audit_blind(gateway, store, spec, envelope, model=auditor)
        failed_on = ["DROPPED"] if verdict == "FAIL" else []
    usd, ptok, ctok = _meter(results)
    return Call(
        auditor=auditor, arm=arm, item_id=item.item_id, domain=item.domain, position=position, rep=rep,
        verdict=verdict, failed_on=failed_on, reply=reply, findings=findings, critical_findings=critical,
        usd=usd, prompt_tokens=ptok, completion_tokens=ctok, seconds=round(time.monotonic() - t0, 1),
    )


# --- reporting ------------------------------------------------------------------------------------------

def _majority(calls: list[dict[str, Any]]) -> bool:
    """True = FAIL by majority of replications (the verdict the item gets)."""
    fails = sum(c["verdict"] == "FAIL" for c in calls)
    return fails * 2 > len(calls)


def report(path: Path) -> str:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    by: dict[tuple[str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        by[(r["auditor"], r["arm"], r["item_id"], r["position"])].append(r)
    auditors = sorted({r["auditor"] for r in rows})
    lines = [f"# blind_audit — {len(rows)} auditor calls, US$ {sum(r['usd'] or 0 for r in rows):.4f}", ""]
    for auditor in auditors:
        lines.append(f"## auditor `{auditor}`")
        lines.append("")
        lines.append("| arm | position | items | FAIL (majority) | Wilson 95% | flip rate | mean tokens/item | US$ |")
        lines.append("|---|---|---:|---:|---|---:|---:|---:|")
        verdicts: dict[tuple[str, str], dict[str, bool]] = defaultdict(dict)
        for arm in ARMS:
            for pos in POSITIONS:
                keys = [k for k in by if k[0] == auditor and k[1] == arm and k[3] == pos]
                if not keys:
                    continue
                fails, flips, toks, usd = 0, 0, 0, 0.0
                for k in keys:
                    calls = by[k]
                    maj = _majority(calls)
                    verdicts[(arm, pos)][k[2]] = maj
                    fails += maj
                    if len({c["verdict"] for c in calls}) > 1:
                        flips += 1
                    toks += sum(c["prompt_tokens"] + c["completion_tokens"] for c in calls) / len(calls)
                    usd += sum(c["usd"] or 0 for c in calls)
                n = len(keys)
                lo, hi = wilson_bounds(fails, n)
                lines.append(f"| `{arm}` | {pos} | {n} | **{fails}/{n}** | [{lo:.2f}, {hi:.2f}] | "
                             f"{flips}/{n} | {toks / n:,.0f} | {usd:.4f} |")
        lines.append("")
        for treat in ("blind", "shipped_dropped_only"):
            for pos in POSITIONS:
                s, b = verdicts.get(("shipped", pos), {}), verdicts.get((treat, pos), {})
                common = sorted(set(s) & set(b))
                if not common:
                    continue
                pr = compare_paired([s[i] for i in common], [b[i] for i in common],
                                    baseline_name="shipped", treatment_name=treat)
                lo, hi = pr.diff_ci
                lines.append(f"- **{pos}** paired FAIL, shipped → {treat}: {pr.baseline_rate:.2f} → {pr.treatment_rate:.2f} "
                             f"(Δ {pr.delta:+.2f}, Newcombe 95% [{lo:+.2f}, {hi:+.2f}]; discordant {pr.discordant}: "
                             f"{treat}-only {pr.treatment_only}, shipped-only {pr.baseline_only}; "
                             f"{'significant' if pr.significant else 'not significant'})")
        for arm in ARMS:
            mid, none = verdicts.get((arm, "middle"), {}), verdicts.get((arm, "none"), {})
            if mid and none:
                d = sum(mid.values()) / len(mid) - sum(none.values()) / len(none)
                lines.append(f"- discrimination `{arm}` (middle FAIL − none FAIL): {d:+.2f}")
        for arm in ("shipped", "shipped_dropped_only"):
            arm_mid = [r for r in rows if r["auditor"] == auditor and r["arm"] == arm and r["position"] == "middle"]
            if not arm_mid:
                continue
            which: dict[str, int] = defaultdict(int)
            for r in arm_mid:
                for key in r["failed_on"]:
                    which[key] += 1
            lines.append(f"- {arm}-arm FAIL lines on middle items (all reps): {dict(which)}")
        lines.append("")
        lines.append("### Every shipped-arm PASS on a middle item (majority), one reply each — read these")
        lines.append("")
        rng = random.Random(7)
        for k in sorted(by):
            if k[0] != auditor or k[1] != "shipped" or k[3] != "middle" or _majority(by[k]):
                continue
            c = rng.choice([c for c in by[k] if c["verdict"] == "PASS"])
            lines.append(f"- `{k[2]}` · {c['reply'].replace(chr(10), ' ')[:400]}")
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", default=str(Path(__file__).with_name("results") / "corpus.jsonl"))
    ap.add_argument("--out", default="")
    ap.add_argument("--report", default="")
    ap.add_argument("--auditors", default="")
    ap.add_argument("--arms", default=",".join(ARMS))
    ap.add_argument("--positions", default=",".join(POSITIONS))
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--limit", type=int, default=0, help="first N corpus items only (pilot)")
    ap.add_argument("--workers", type=int, default=6)
    args = ap.parse_args()
    if args.report:
        print(report(Path(args.report)))
        return 0
    settings = get_settings()
    if not settings.has_any_key():
        print("no provider key in the environment", file=sys.stderr)
        return 2
    if settings.cache:
        print("CHIMERA_CACHE is on — replications would be served from cache; aborting", file=sys.stderr)
        return 2
    from chimera.providers.catalog import resolve_tiers

    tiers = resolve_tiers(settings)
    auditors = args.auditors.split(",") if args.auditors else [tiers.weak, tiers.mid]
    items = load_corpus(Path(args.corpus))
    if args.limit:
        items = items[: args.limit]
    out = Path(args.out) if args.out else Path(__file__).with_name("results") / f"{time.strftime('%Y-%m-%d')}-audit.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    store = ArtifactStore(out.parent / "artifacts")

    # Instrument check over the whole corpus BEFORE the first auditor call (§2q).
    bad = []
    for item in items:
        for pos in POSITIONS:
            _, envelope, planted = make_envelope(item, pos, store)
            problem = instrument_check(item, pos, envelope, planted)
            if problem:
                bad.append((item.item_id, pos, problem))
    if bad:
        for b in bad:
            print(f"  INSTRUMENT {b[0]} {b[1]}: {b[2]}", file=sys.stderr)
        print(f"{len(bad)} (item, position) pairs cannot exhibit the effect — aborting", file=sys.stderr)
        return 3
    print(f"instrument check passed on {len(items)} items × {len(POSITIONS)} positions")

    done = set()
    if out.exists():
        for line in out.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                done.add((r["auditor"], r["arm"], r["item_id"], r["position"], r["rep"]))
    plan = [(a, arm, item, pos, rep)
            for rep in range(args.reps) for item in items for pos in args.positions.split(",")
            for arm in args.arms.split(",") for a in auditors
            if (a, arm, item.item_id, pos, rep) not in done]
    print(f"auditors {auditors}; {len(plan)} calls to make, {len(done)} on disk")
    from concurrent.futures import ThreadPoolExecutor, as_completed

    spent = 0.0
    with out.open("a", encoding="utf-8") as fh, ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(one, item, pos, arm, rep, auditor=a, store=store): (a, arm, item.item_id, pos, rep)
                   for a, arm, item, pos, rep in plan}
        for fut in as_completed(futures):
            a, arm, iid, pos, rep = futures[fut]
            try:
                c = fut.result()
            except Exception as exc:  # noqa: BLE001 — a provider hiccup must not lose the file
                print(f"  {a.split('/')[-1]:<32} {arm:<8} {iid:<22} {pos:<6} r{rep} ERROR {type(exc).__name__}: {str(exc)[:100]}", flush=True)
                continue
            spent += c.usd or 0.0
            fh.write(json.dumps(asdict(c), ensure_ascii=False) + "\n")
            fh.flush()
            print(f"  {a.split('/')[-1]:<32} {arm:<8} {iid:<22} {pos:<6} r{rep} {c.verdict:<4} {c.failed_on} "
                  f"{c.seconds}s  Σ US$ {spent:.4f}", flush=True)
    print(f"written {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
