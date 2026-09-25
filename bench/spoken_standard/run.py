"""H9 — the spoken-output standard: the shipped ``SPOKEN_NOTE`` against a rewrite that says what to do.

    python bench/spoken_standard/run.py --check                 # the instrument, printed; no calls
    python bench/spoken_standard/run.py --run [--limit N]       # the paid run (resumes by default)
    python bench/spoken_standard/run.py --report results/run.json

Registered in ``PREREGISTRATION.md`` before any call. Arm A is ``chimera.api.code_api.SPOKEN_NOTE``
imported, byte for byte; arm B is the frozen rewrite below, refused if its hash moved; arm N (no note)
is the positive control. Each arm's system prompt is ``DEFAULT_SYSTEM_PROMPT`` plus the note, joined
the way the Code screen joins it. No tools: the text answer is all that is measured, by
``checker.py``, at report time from the stored answers — a fix to the checker never costs a call.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
import subprocess
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
if hasattr(sys.stdout, "reconfigure"):  # absent under pytest's capture
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from bench.spoken_standard.checker import ALL_KEYS, CONTENT_KEYS, check  # noqa: E402
from bench.spoken_standard.corpus import KINDS, corpus  # noqa: E402

HERE = Path(__file__).resolve().parent
DEFAULT_OUT = HERE / "results" / "run.json"

MODEL = "openrouter/deepseek/deepseek-v4-flash-0731"
TEMPERATURE = 0.2
MAX_TOKENS = 1500
REPLICAS = 2
BUDGET_USD = 0.30
#: A conservative per-token bound for the budget guard: the higher price the catalogue has seen for
#: this slug (0.065 / 0.18 per million), not the current one, so the cap errs toward stopping early.
_BOUND_IN, _BOUND_OUT = 0.065e-6, 0.18e-6

NOTE_B = (
    "The person said this aloud, and a voice will read your answer to them before they see it on a "
    "screen. Answer for the ear: the gist first, in two to four short spoken sentences of plain "
    "words a listener can follow. Say numbers, dates and versions as people say them. After the "
    "spoken part, put a line containing only --- and write anything exact below it (a plan, a list "
    "of files, code, a command, an ID, a path, a link): the voice reads what is above the line, and "
    "the screen shows all of it."
)
NOTE_B_SHA256 = "00034da6bc8d7f8b2fd89489c980cc0d3bdc496e33f9b8df958ce786e50581e8"  # frozen in PREREGISTRATION.md


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def notes() -> dict[str, str | None]:
    """The three arms' notes. A is imported, never copied: the shipped text is the arm."""
    from chimera.api.code_api import SPOKEN_NOTE

    if sha256(NOTE_B) != NOTE_B_SHA256:
        raise SystemExit(f"arm B's text moved since registration: {sha256(NOTE_B)} != {NOTE_B_SHA256}")
    return {"A": SPOKEN_NOTE, "B": NOTE_B, "N": None}


def system_prompt(note: str | None) -> str:
    """The Code screen's join (`code_api.py`: ``system_prompt += f"\\n\\n{SPOKEN_NOTE}"``)."""
    from chimera.core.agent import DEFAULT_SYSTEM_PROMPT

    return DEFAULT_SYSTEM_PROMPT if note is None else f"{DEFAULT_SYSTEM_PROMPT}\n\n{note}"


def schedule(index: int) -> list[str]:
    """ABBA on even requests, BAAB on odd ones, then the control — so neither arm always goes first."""
    return (["A", "B", "B", "A"] if index % 2 == 0 else ["B", "A", "A", "B"]) + ["N"]


def instrument_check() -> None:
    arms = notes()
    for arm, note in arms.items():
        prompt = system_prompt(note)
        size = "—" if note is None else f"{len(note)} chars, {len(note.split())} words"
        print(f"arm {arm}: note {size}; system prompt {len(prompt)} chars; sha256 {sha256(prompt)[:16]}")
    a, b = arms["A"], arms["B"]
    assert a is not None and b is not None
    assert len(b) <= len(a), "B must be the same length as A or shorter"
    assert "---" in b, "B keeps the --- convention"
    items = corpus()
    print(f"\ncorpus: {len(items)} requests; kinds {dict(Counter(r.kind for r in items))}; "
          f"langs {dict(Counter(r.lang for r in items))}")
    for i, req in enumerate(items):
        print(f"  {req.id:<11} {req.lang} {''.join(schedule(i))}  {req.text}")
    print(f"\ncalls: {len(items) * (2 * REPLICAS + 1)}; model {MODEL}; temperature {TEMPERATURE}; "
          f"reasoning off; max_tokens {MAX_TOKENS}; cap US$ {BUDGET_USD}")


def _git_head() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True, text=True,
                              check=False).stdout.strip()
    except OSError:
        return ""


def _call(gateway: Any, prompt: str, text: str) -> dict[str, Any]:
    """One answer. Reasoning off the way the voice mode asks for it, passed explicitly because
    ``LLMGateway.complete`` accepts ``thinking`` and does not forward it (only the streaming path
    does); the request body is the one ``_provider_kwargs(thinking=False)`` builds."""
    from chimera.orchestration.receipts import price_completion

    extra = gateway._provider_kwargs(MODEL, thinking=False)
    messages = [{"role": "system", "content": prompt}, {"role": "user", "content": text}]
    last_error = ""
    for attempt in range(3):
        started = time.monotonic()
        try:
            result = gateway.complete(messages, model=MODEL, temperature=TEMPERATURE,
                                      max_tokens=MAX_TOKENS, extra_body=extra.get("extra_body"))
        except Exception as exc:  # noqa: BLE001 — recorded, retried, then a halt
            last_error = f"{type(exc).__name__}: {str(exc)[:200]}"
            time.sleep(2 + 3 * attempt)
            continue
        latency = time.monotonic() - started
        content = result.content or ""
        cost = price_completion(result)
        pt, ct = result.prompt_tokens, result.completion_tokens
        bound = (pt if isinstance(pt, int) else 2000) * _BOUND_IN + (
            ct if isinstance(ct, int) else MAX_TOKENS) * _BOUND_OUT
        row = {
            "content": content,
            "truncated": bool(getattr(result, "truncated", False)),
            "prompt_tokens": pt,
            "completion_tokens": ct,
            "cache_read_tokens": getattr(result, "cache_read_tokens", None),
            "provider": getattr(result, "provider", ""),
            "generation_id": getattr(result, "generation_id", ""),
            "model_answered": result.model,
            "latency_s": round(latency, 3),
            "usd_priced": None if cost.unpriced else cost.usd,
            "usd_bound": round(max(bound, cost.usd or 0.0), 8),
            "attempts": attempt + 1,
            "error": "",
        }
        if content.strip():
            return row
        # An empty reply is the route failing, not the model choosing a form: re-asked, then a halt
        # (PREREGISTRATION.md, stop rule) — never scored as an answer with nothing above the line.
        last_error = "empty content"
        time.sleep(1)
    return {"content": "", "error": last_error or "empty content", "usd_bound": 2000 * _BOUND_IN
            + MAX_TOKENS * _BOUND_OUT, "attempts": 3}


def run(out: Path, limit: int | None) -> None:
    from chimera.providers import LLMGateway

    arms = notes()
    prompts = {arm: system_prompt(note) for arm, note in arms.items()}
    gateway = LLMGateway()
    data: dict[str, Any] = {"meta": {}, "rows": []}
    if out.exists():
        data = json.loads(out.read_text(encoding="utf-8"))
        print(f"resuming: {len(data['rows'])} calls already recorded")
    data["meta"] = {
        "model": MODEL, "temperature": TEMPERATURE, "max_tokens": MAX_TOKENS, "replicas": REPLICAS,
        "reasoning": "off", "budget_usd": BUDGET_USD, "git_head": _git_head(),
        "note_sha256": {arm: (sha256(n) if n else None) for arm, n in arms.items()},
        "system_prompt_sha256": {arm: sha256(p) for arm, p in prompts.items()},
        "checker_sha256": sha256((HERE / "checker.py").read_text(encoding="utf-8")),
        "corpus_sha256": sha256((HERE / "corpus.py").read_text(encoding="utf-8")),
        "started": data.get("meta", {}).get("started") or time.strftime("%Y-%m-%dT%H:%M:%S"),
        "stopped_by_budget": False,
    }
    done = {(r["request"], r["position"]) for r in data["rows"] if not r.get("error")}
    data["rows"] = [r for r in data["rows"] if not r.get("error")]  # a failed cell is asked again
    spent = sum(r.get("usd_bound") or 0.0 for r in data["rows"])
    items = corpus()
    for index, req in enumerate(items[: limit] if limit else items):
        seen: Counter[str] = Counter()
        for position, arm in enumerate(schedule(index)):
            seen[arm] += 1
            if (req.id, position) in done:
                continue
            if spent >= BUDGET_USD:
                data["meta"]["stopped_by_budget"] = True
                print(f"budget cap reached at US$ {spent:.4f}; stopping")
                break
            row = _call(gateway, prompts[arm], req.text)
            spent += row.get("usd_bound") or 0.0
            row.update({"request": req.id, "kind": req.kind, "lang": req.lang, "arm": arm,
                        "replica": seen[arm], "position": position})
            data["rows"].append(row)
            flag = row["error"] or ("speakable" if check(row["content"]).speakable else "violations")
            print(f"  {req.id:<11} {arm}{seen[arm]} {flag:<10} {row.get('provider', '')!s:<14} "
                  f"US$ {spent:.4f}")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        if data["meta"]["stopped_by_budget"]:
            break
    print(f"wrote {out} — {len(data['rows'])} calls, bound US$ {spent:.4f}")


# --- statistics --------------------------------------------------------------------------------------


def exact_binomial_two_sided(k_small: int, n: int) -> float:
    """Exact two-sided p for a fair coin (McNemar's exact test on the discordant pairs)."""
    if n == 0:
        return 1.0
    tail = sum(math.comb(n, i) for i in range(0, min(k_small, n - k_small) + 1)) / 2**n
    return min(1.0, 2 * tail)


def wilson(successes: int, n: int, z: float = 1.959964) -> tuple[float, float]:
    if n == 0:
        return (0.0, 1.0)
    p = successes / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def newcombe_paired(a: int, b: int, c: int, d: int) -> tuple[float, float, float]:
    """B − A with Newcombe's hybrid-score interval for paired proportions (method 10).

    a: both speakable · b: A only · c: B only · d: neither."""
    n = a + b + c + d
    if n == 0:
        return (0.0, -1.0, 1.0)
    p_a, p_b = (a + b) / n, (a + c) / n
    l_a, u_a = wilson(a + b, n)
    l_b, u_b = wilson(a + c, n)
    denom = math.sqrt((a + b) * (c + d) * (a + c) * (b + d))
    phi = (a * d - b * c) / denom if denom else 0.0
    theta = p_b - p_a
    lower = theta - math.sqrt(max(0.0, (p_b - l_b) ** 2 - 2 * phi * (p_b - l_b) * (u_a - p_a) + (u_a - p_a) ** 2))
    upper = theta + math.sqrt(max(0.0, (u_b - p_b) ** 2 - 2 * phi * (u_b - p_b) * (p_a - l_a) + (p_a - l_a) ** 2))
    return (theta, lower, upper)


# --- report ------------------------------------------------------------------------------------------


def _arm_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    checks = [check(r["content"]) for r in rows]
    n = len(checks)
    ok = sum(c.speakable for c in checks)
    fmt = sum(c.format_speakable for c in checks)
    words = [c.words for c in checks]
    return {
        "n": n,
        "speakable": ok, "speakable_rate": ok / n if n else None, "speakable_ci95": wilson(ok, n),
        "format_speakable": fmt, "format_speakable_rate": fmt / n if n else None,
        "answers_with": {k: sum(1 for c in checks if c.counts[k]) for k in ALL_KEYS},
        "counts": {k: sum(c.counts[k] for c in checks) for k in ALL_KEYS},
        "answers_with_markdown": sum(1 for c in checks if c.markdown),
        "answers_with_content_violation": sum(1 for c in checks if any(c.counts[k] for k in CONTENT_KEYS)),
        "rule_rate": sum(c.has_rule for c in checks) / n if n else None,
        "spoken_words_mean": statistics.fmean(words) if words else None,
        "spoken_words_median": statistics.median(words) if words else None,
        "spoken_chars_mean": statistics.fmean([c.chars for c in checks]) if checks else None,
        "sentences_mean": statistics.fmean([c.sentences for c in checks]) if checks else None,
        "completion_tokens_mean": statistics.fmean([r["completion_tokens"] for r in rows
                                                    if isinstance(r.get("completion_tokens"), int)] or [0]),
        "truncated": sum(1 for r in rows if r.get("truncated")),
        "latency_median_s": statistics.median([r["latency_s"] for r in rows]) if rows else None,
        "usd_priced": round(sum(r.get("usd_priced") or 0.0 for r in rows), 6),
        "unpriced_calls": sum(1 for r in rows if r.get("usd_priced") is None),
        "providers": dict(Counter(r.get("provider") or "?" for r in rows)),
    }


def _paired(by: dict[tuple[str, str, int], dict[str, Any]], requests: list[str], outcome: str) -> dict[str, Any]:
    a = b = c = d = 0
    per_request: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0])  # [A ok, B ok, pairs]
    for req in requests:
        for rep in range(1, REPLICAS + 1):
            ra, rb = by.get((req, "A", rep)), by.get((req, "B", rep))
            if ra is None or rb is None:
                continue
            ca, cb = check(ra["content"]), check(rb["content"])
            oa, ob = getattr(ca, outcome), getattr(cb, outcome)
            a += oa and ob
            b += oa and not ob
            c += ob and not oa
            d += not oa and not ob
            per_request[req][0] += oa
            per_request[req][1] += ob
            per_request[req][2] += 1
    theta, lo, hi = newcombe_paired(a, b, c, d)
    up = sum(1 for v in per_request.values() if v[1] > v[0])
    down = sum(1 for v in per_request.values() if v[1] < v[0])
    return {
        "pairs": a + b + c + d, "both": a, "A_only": b, "B_only": c, "neither": d,
        "mcnemar_p": exact_binomial_two_sided(min(b, c), b + c),
        "diff_B_minus_A": theta, "newcombe95": (lo, hi),
        "requests_B_better": up, "requests_A_better": down,
        "request_sign_p": exact_binomial_two_sided(min(up, down), up + down),
    }


def _replay_floor(by: dict[tuple[str, str, int], dict[str, Any]], requests: list[str], arm: str) -> dict[str, Any]:
    both = flips = 0
    for req in requests:
        r1, r2 = by.get((req, arm, 1)), by.get((req, arm, 2))
        if r1 is None or r2 is None:
            continue
        both += 1
        flips += check(r1["content"]).speakable != check(r2["content"]).speakable
    return {"requests": both, "flips": flips, "rate": flips / both if both else None}


def report(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    rows = data["rows"]
    halts = [r for r in rows if r.get("error")]
    good = [r for r in rows if not r.get("error")]
    by = {(r["request"], r["arm"], r["replica"]): r for r in good}
    requests = [r.id for r in corpus()]
    arms = {arm: _arm_summary([r for r in good if r["arm"] == arm]) for arm in ("A", "B", "N")}
    paired = _paired(by, requests, "speakable")
    paired_fmt = _paired(by, requests, "format_speakable")
    floors = {arm: _replay_floor(by, requests, arm) for arm in ("A", "B")}

    per_kind = {kind: {arm: _rate([r for r in good if r["arm"] == arm and r["kind"] == kind])
                       for arm in ("A", "B", "N")} for kind in KINDS}
    per_lang = {lang: {arm: _rate([r for r in good if r["arm"] == arm and r["lang"] == lang])
                       for arm in ("A", "B", "N")} for lang in ("pt", "en")}

    wa, wb = arms["A"]["spoken_words_mean"] or 0.0, arms["B"]["spoken_words_mean"] or 0.0
    length_ratio = wb / wa if wa else None
    control_gap = (arms["A"]["speakable_rate"] or 0.0) - (arms["N"]["speakable_rate"] or 0.0)
    control_ok = control_gap >= 0.20
    halt_rate = len(halts) / len(rows) if rows else 0.0

    if halt_rate > 0.10:
        decision = "INCONCLUSIVE: more than 10% of calls halted"
    elif not control_ok:
        decision = "NO DECISION: the positive control failed (A is not 20 pp above N)"
    elif paired["B_only"] > paired["A_only"] and paired["mcnemar_p"] < 0.05:
        if length_ratio is not None and length_ratio <= 1.20:
            decision = "ADOPT B: speakable rises (McNemar p < 0.05) and spoken length grows <= 20%"
        else:
            decision = "NULL: speakable rises, but the spoken part grows more than 20%"
    elif paired["A_only"] > paired["B_only"] and paired["mcnemar_p"] < 0.05:
        decision = "B WORSE: speakable falls (McNemar p < 0.05); not adopted"
    else:
        decision = "NULL: no difference in speakable at p < 0.05"

    summary = {
        "meta": data["meta"], "calls": len(rows), "halts": len(halts), "arms": arms,
        "paired_speakable": paired, "paired_format_speakable": paired_fmt, "replay_floor": floors,
        "per_kind": per_kind, "per_lang": per_lang, "length_ratio_B_over_A": length_ratio,
        "positive_control": {"A_minus_N": control_gap, "passes": control_ok},
        "usd_priced_total": round(sum(r.get("usd_priced") or 0.0 for r in good), 6),
        "usd_bound_total": round(sum(r.get("usd_bound") or 0.0 for r in rows), 6),
        "decision": decision,
    }
    _print(summary)
    out = path.with_name(path.stem + "-summary.json")
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\nwrote {out}")
    return summary


def _rate(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "—"
    ok = sum(check(r["content"]).speakable for r in rows)
    return f"{ok}/{len(rows)}"


def _print(s: dict[str, Any]) -> None:
    print(f"calls {s['calls']} · halts {s['halts']} · priced US$ {s['usd_priced_total']:.4f} "
          f"(bound {s['usd_bound_total']:.4f})\n")
    head = f"{'':<28}" + "".join(f"{arm:>12}" for arm in ("A", "B", "N"))
    print(head)

    def line(label: str, fn: Any) -> None:
        print(f"{label:<28}" + "".join(f"{fn(s['arms'][arm]):>12}" for arm in ("A", "B", "N")))

    line("answers", lambda a: a["n"])
    line("speakable", lambda a: f"{a['speakable']} ({a['speakable_rate']:.0%})" if a["n"] else "—")
    line("format-speakable", lambda a: f"{a['format_speakable']} ({a['format_speakable_rate']:.0%})" if a["n"] else "—")
    line("with markdown", lambda a: a["answers_with_markdown"])
    for key in ALL_KEYS:
        line(f"  with {key}", lambda a, k=key: a["answers_with"][k])
    line("used the --- line", lambda a: f"{a['rule_rate']:.0%}" if a["n"] else "—")
    line("spoken words (mean)", lambda a: f"{a['spoken_words_mean']:.1f}" if a["n"] else "—")
    line("sentences (mean)", lambda a: f"{a['sentences_mean']:.2f}" if a["n"] else "—")
    line("completion tokens (mean)", lambda a: f"{a['completion_tokens_mean']:.0f}")
    line("truncated", lambda a: a["truncated"])
    line("latency median (s)", lambda a: f"{a['latency_median_s']:.2f}" if a["n"] else "—")
    for name, key in (("speakable", "paired_speakable"), ("format-speakable", "paired_format_speakable")):
        p = s[key]
        print(f"\npaired {name}: {p['pairs']} pairs · both {p['both']} · A only {p['A_only']} · "
              f"B only {p['B_only']} · neither {p['neither']}")
        print(f"  B − A = {p['diff_B_minus_A']:+.1%}  [{p['newcombe95'][0]:+.1%}, {p['newcombe95'][1]:+.1%}]  "
              f"McNemar exact p = {p['mcnemar_p']:.4f}")
        print(f"  request level: B better on {p['requests_B_better']}, A better on {p['requests_A_better']}, "
              f"sign p = {p['request_sign_p']:.4f}")
    for arm, f in s["replay_floor"].items():
        print(f"replay floor {arm}: {f['flips']}/{f['requests']} requests flip speakable between replicas")
    print("\nper kind (speakable):")
    for kind, v in s["per_kind"].items():
        print(f"  {kind:<9} A {v['A']:>6}  B {v['B']:>6}  N {v['N']:>6}")
    print("per language (speakable):")
    for lang, v in s["per_lang"].items():
        print(f"  {lang:<9} A {v['A']:>6}  B {v['B']:>6}  N {v['N']:>6}")
    ratio = s["length_ratio_B_over_A"]
    print(f"\nspoken length B/A: {ratio:.3f}" if ratio else "\nspoken length B/A: —")
    pc = s["positive_control"]
    print(f"positive control A − N: {pc['A_minus_N']:+.1%} ({'passes' if pc['passes'] else 'FAILS'})")
    print(f"\nDECISION: {s['decision']}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--limit", type=int, default=None, help="only the first N requests (a smoke run)")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--report", type=Path, default=None)
    args = ap.parse_args()
    if args.check:
        instrument_check()
    elif args.run:
        run(args.out, args.limit)
    elif args.report:
        report(args.report)
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
