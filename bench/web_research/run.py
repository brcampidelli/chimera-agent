"""S12: does the research module make a web answer's citations real, and at what cost?

    python bench/web_research/run.py --check-gold              # free: the answer key, before any call
    python bench/web_research/run.py --run [--replicas 2]      # A1 B1 A2 B2, item by item
    python bench/web_research/run.py --verify results/run.json # free: fetch every cited URL, grade
    python bench/web_research/run.py --report results/run.json

See PREREGISTRATION.md, registered before any paid call. Arm A is the plain loop (the default system
prompt) with the research tools; arm B is `WebResearcher`, the research sub-agent, with the same tools.
Both get the same question text, the same step ceiling and the same pinned endpoint. Everything that
decides the outcome (answer match, which cited URLs a tool result contained, whether a cited page
contains the claimed answer) is deterministic and needs no judge.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
import unicodedata
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

from items import ALL, HARD, Item  # noqa: E402

HERE = Path(__file__).resolve().parent
MODEL = "openrouter/deepseek/deepseek-v4-flash-0731"
PROVIDER = "DeepInfra"
STEPS = 12  # arm B at medium thoroughness; arm A gets the same ceiling
SPEND_STOP = 1.40  # US$, catalogue-priced; the cap is 2.00 and the served price may differ

#: The same words follow every question in both arms. Asking for the URLs in both arms is what makes
#: the citation metric defined for the plain loop; the answer line is what makes grading exact.
SUFFIX = (
    "\n\nCite the URL of each page you relied on, and end your reply with one line of the form "
    '"ANSWER: <your answer>".'
)
#: What the agent could see of a page: the scrape tool keeps the first 20,000 characters.
VISIBLE_CHARS = 20_000


class _Pinned:
    """The gateway, with every call pinned to one OpenRouter provider and no fallbacks."""

    def __init__(self) -> None:
        from chimera.providers import LLMGateway

        self.gateway = LLMGateway()

    def complete(self, messages: Any, **kwargs: Any) -> Any:
        kwargs["extra_body"] = {"provider": {"order": [PROVIDER], "allow_fallbacks": False}}
        return self.gateway.complete(messages, **kwargs)


# ---- grading (pure) -----------------------------------------------------------------------------

_ANSWER = re.compile(r"^[\W_]*answer[\W_]*:\s*(?P<value>.+?)\s*$", re.IGNORECASE | re.MULTILINE)


def norm(text: str) -> str:
    """Case, accents and punctuation folded away; words separated by one space."""
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch)).casefold()
    return " ".join(re.sub(r"[^0-9a-zø]+", " ", stripped).split())


def answer_value(text: str) -> str:
    """The value of the LAST `ANSWER:` line, or "" when there is none (graded wrong)."""
    found = list(_ANSWER.finditer(text or ""))
    return found[-1].group("value").strip().strip("*").strip() if found else ""


def contains(haystack: str, needle: str) -> bool:
    """Whole-word containment after folding."""
    n = norm(needle)
    return bool(n) and f" {n} " in f" {norm(haystack)} "


def correct(item: Item, value: str) -> bool:
    return any(contains(value, alias) for alias in item.answers)


def mcnemar_exact(b: int, c: int) -> float:
    n = b + c
    if n == 0:
        return 1.0
    return min(1.0, 2 * sum(math.comb(n, k) for k in range(0, min(b, c) + 1)) / 2**n)


# ---- the answer key, before any call ------------------------------------------------------------

_PAGES: dict[str, tuple[int | None, str]] = {}


def fetch_text(url: str) -> tuple[int | None, str]:
    """A page's full text by plain HTTP, cached for the process. Never the agent's truncated view.

    Amendment 1: a 429 is waited out (up to three times, 10 s apart) rather than recorded, because
    Wikipedia rate-limits a burst of grading fetches and a 429 says nothing about the page."""
    import time

    from chimera.scrape.fetch import fetch_page

    for attempt in range(4):
        if url in _PAGES:
            break
        try:
            page = fetch_page(url, render="http")
        except Exception as exc:  # noqa: BLE001 — an unreachable page is a failed check, not a crash
            _PAGES[url] = (None, f"error: {type(exc).__name__}: {exc}")
            break
        if page.status == 429 and attempt < 3:
            time.sleep(10)
            continue
        _PAGES[url] = (page.status, page.markdown)
    return _PAGES[url]


def check_gold() -> bool:
    """Every hop page answers 200; the gold page contains the answer; the next item's does not."""
    ok = True
    for index, item in enumerate(ALL):
        statuses = [fetch_text(url)[0] for url in item.sources]
        status, text = fetch_text(item.gold)
        full = any(contains(text, a) for a in item.answers)
        visible = any(contains(text[:VISIBLE_CHARS], a) for a in item.answers)
        other = ALL[(index + 1) % len(ALL)]
        control = any(contains(fetch_text(other.gold)[1], a) for a in item.answers)
        good = all(s == 200 for s in statuses) and full
        ok &= good
        print(f"{'ok  ' if good else 'FAIL'} {item.id:<26} hops={item.hops} statuses={statuses} "
              f"answer-on-gold={full} in-first-20k={visible} on-{other.id}={control}")
    return ok


# ---- the run ------------------------------------------------------------------------------------


def _turn(backend: _Pinned, arm: str, item: Item) -> dict[str, Any]:
    from chimera.core.agent import Agent, AgentConfig
    from chimera.core.research import (
        SourceLog,
        WebResearcher,
        check_citations,
        web_research_registry,
    )

    registry = web_research_registry()
    question = item.question + SUFFIX
    try:
        if arm == "A":
            log = SourceLog()
            agent = Agent(
                backend,  # type: ignore[arg-type]
                registry,
                AgentConfig(model=MODEL, max_steps=STEPS, prefix_nonce=""),
            )
            result = agent.run(question, on_tool=log.record)
            check = check_citations(result.answer, log)
            answer, steps, calls = result.answer, result.steps, result.tool_calls_made
            prompt, completion, usd, stopped = (
                result.prompt_tokens, result.completion_tokens, result.usd, result.stopped_reason
            )
            cached = result.cache_read_tokens
        else:
            researcher = WebResearcher(
                backend, registry=registry, model=MODEL, max_turns=STEPS  # type: ignore[arg-type]
            )
            got = researcher.research(question, "medium")
            if got.error:
                raise RuntimeError(got.error)
            check, answer, steps, calls = got.check, got.answer, got.steps, got.tool_calls
            prompt, completion, usd, stopped = (
                got.prompt_tokens, got.completion_tokens, got.usd, got.stopped_reason
            )
            cached = got.cache_read_tokens
        return {
            "error": None, "answer": answer[:3000], "value": answer_value(answer),
            "cited": list(check.cited), "unverified": list(check.unverified),
            "steps": steps, "tool_calls": calls, "stopped": stopped,
            "prompt_tokens": prompt, "completion_tokens": completion, "cache_read_tokens": cached,
            "usd": usd, "registry": sorted(registry.names()),
        }
    except Exception as exc:  # noqa: BLE001 — a provider failure is counted by the stop rule
        return {"error": f"{type(exc).__name__}: {exc}"[:300]}


def _item(backend: _Pinned, item: Item, replicas: int) -> dict[str, Any]:
    """One item's turns, in the registered order A1 B1 A2 B2."""
    stratum = "hard" if item in HARD else "registered"
    row: dict[str, Any] = {"id": item.id, "stratum": stratum, "runs": {"A": [], "B": []}}
    for _replica in range(replicas):
        for arm in ("A", "B"):
            row["runs"][arm].append(_turn(backend, arm, item))
    return row


def run(out: Path, replicas: int, workers: int, only: int | None) -> None:
    from concurrent.futures import ThreadPoolExecutor, as_completed

    items = ALL[:only] if only else ALL
    backend = _Pinned()
    rows: list[dict[str, Any]] = []
    errors = {"A": 0, "B": 0}
    turns = {"A": 0, "B": 0}
    usd = 0.0
    stopped = ""
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_item, backend, item, replicas): item for item in items}
        for done, future in enumerate(as_completed(futures), 1):
            item, row = futures[future], future.result()
            rows.append(row)
            marks = {}
            for arm in ("A", "B"):
                cells = []
                for got in row["runs"][arm]:
                    turns[arm] += 1
                    usd += got.get("usd") or 0.0
                    if got["error"]:
                        errors[arm] += 1
                        cells.append("E")
                    else:
                        cells.append(("C" if correct(item, got["value"]) else "w")
                                     + str(len(got["unverified"])))
                marks[arm] = " ".join(cells)
            print(f"  [{done:>2}/{len(items)}] {item.id:<26} A={marks['A']:<7} B={marks['B']:<7} "
                  f"US${usd:.3f}", flush=True)
            for arm in ("A", "B"):
                if not stopped and turns[arm] >= 10 and errors[arm] / turns[arm] > 0.10:
                    stopped = f"stop rule: arm {arm} errored on {errors[arm]}/{turns[arm]} turns"
            if not stopped and usd >= SPEND_STOP:
                stopped = f"stop rule: spend reached US${usd:.3f}"
            if stopped:
                print(stopped.upper())
                for pending in futures:
                    pending.cancel()
                break
    order = [i.id for i in ALL]
    rows.sort(key=lambda r: order.index(r["id"]))
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {"model": MODEL, "provider": PROVIDER, "steps": STEPS, "suffix": SUFFIX, "usd": usd,
               "errors": errors, "stopped": stopped, "rows": rows}
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8",
                   newline="\n")
    print(f"\nwrote {out}  —  US${usd:.4f}, errors {errors}")


# ---- grading after the run (free) ---------------------------------------------------------------


def verify(path: Path) -> None:
    """Fetch every cited URL once and record, per turn, whether a cited page holds the claim."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    by_id = {i.id: i for i in ALL}
    for row in payload["rows"]:
        item = by_id[row["id"]]
        for arm in ("A", "B"):
            for got in row["runs"][arm]:
                if got.get("error"):
                    continue
                is_right = correct(item, got["value"])
                claims = list(item.answers) if is_right else [got["value"]]
                holders = []
                for url in got["cited"]:
                    status, text = fetch_text(url)
                    if status == 200 and any(len(norm(c)) >= 3 and contains(text, c) for c in claims):
                        holders.append(url)
                got["correct"] = is_right
                got["holders"] = holders
    payload["verified"] = True
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8",
                    newline="\n")
    print(f"verified {path}")


def _pairs(rows: list[dict[str, Any]], key: Any) -> tuple[int, int, int, int, int]:
    """(A yes, B yes, only A, only B, pairs) over replica-aligned pairs with no error."""
    a_yes = b_yes = only_a = only_b = n = 0
    for row in rows:
        for ra, rb in zip(row["runs"]["A"], row["runs"]["B"], strict=False):
            if ra.get("error") or rb.get("error"):
                continue
            n += 1
            ka, kb = bool(key(ra)), bool(key(rb))
            a_yes += ka
            b_yes += kb
            only_a += ka and not kb
            only_b += kb and not ka
    return a_yes, b_yes, only_a, only_b, n


def _all_seen(got: dict[str, Any]) -> bool:
    return bool(got["cited"]) and not got["unverified"]


def report(path: Path) -> None:
    """Pooled first (the registered primary, Amendment 1), then each stratum on its own."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    print(f"model {payload['model']} via {payload['provider']}  steps {payload['steps']}  "
          f"US${payload['usd']:.4f}  errors {payload['errors']}  {payload.get('stopped') or ''}")
    if not payload.get("verified"):
        print("(run --verify first for the content metric)")
    everything = payload["rows"]
    for label in ("pooled", "registered", "hard"):
        rows = [r for r in everything if label == "pooled" or r.get("stratum") == label]
        if rows:
            print(f"\n======== {label}: {len(rows)} items")
            _summary(rows)


def _summary(rows: list[dict[str, Any]]) -> None:
    metrics = [
        ("PRIMARY every cited URL seen in tool output (>=1 cited)", _all_seen),
        ("GUARD answer correct", lambda g: g.get("correct")),
        ("content: a cited page holds the claimed answer", lambda g: g.get("holders")),
        ("correct AND a seen, cited page holds it", lambda g: g.get("correct") and any(
            u not in g["unverified"] for u in g.get("holders", []))),
        ("cites at least one URL", lambda g: g["cited"]),
    ]
    for label, key in metrics:
        a, b, oa, ob, n = _pairs(rows, key)
        print(f"{label}:\n  A {a}/{n}  B {b}/{n}  only A {oa}, only B {ob}, "
              f"exact McNemar p = {mcnemar_exact(oa, ob):.4g}")

    for arm in ("A", "B"):
        runs = [g for row in rows for g in row["runs"][arm] if not g.get("error")]
        cited = sum(len(g["cited"]) for g in runs)
        unseen = sum(len(g["unverified"]) for g in runs)
        held = sum(len(g.get("holders", [])) for g in runs)
        tokens = [g["prompt_tokens"] + g["completion_tokens"] for g in runs]
        cached = sum(g.get("cache_read_tokens") or 0 for g in runs)
        usd = sum(g.get("usd") or 0.0 for g in runs)
        rights = sum(1 for g in runs if g.get("correct"))
        print(f"arm {arm}: turns {len(runs)}  cited URLs {cited}, seen {cited - unseen} "
              f"({(cited - unseen) / max(1, cited):.1%}), holding the claim {held}  "
              f"tokens/answer {sum(tokens) / max(1, len(tokens)):,.0f} "
              f"(cache-read {cached / max(1, sum(tokens)):.1%})  "
              f"steps {sum(g['steps'] for g in runs) / max(1, len(runs)):.1f}  "
              f"US$ {usd:.4f}  US$/correct {usd / max(1, rights):.4f}  "
              f"stopped {sorted({g['stopped'] for g in runs})}")

    for arm in ("A", "B"):
        dis = n = 0
        for row in rows:
            got = [g for g in row["runs"][arm] if not g.get("error")]
            if len(got) >= 2:
                n += 1
                dis += int(_all_seen(got[0]) != _all_seen(got[1]))
        print(f"FLOOR replica disagreement on the primary, arm {arm}: {dis}/{n}")
    for arm in ("A", "B"):
        dis = n = 0
        for row in rows:
            got = [g for g in row["runs"][arm] if not g.get("error")]
            if len(got) >= 2:
                n += 1
                dis += int(bool(got[0].get("correct")) != bool(got[1].get("correct")))
        print(f"FLOOR replica disagreement on correctness, arm {arm}: {dis}/{n}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check-gold", action="store_true")
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--verify", type=Path)
    ap.add_argument("--report", type=Path)
    ap.add_argument("--replicas", type=int, default=2)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--only", type=int, default=None, help="the first N items (a pilot)")
    ap.add_argument("--out", type=Path, default=HERE / "results" / "run.json")
    args = ap.parse_args()
    if args.check_gold:
        raise SystemExit(0 if check_gold() else 1)
    if args.verify:
        verify(args.verify)
    elif args.report:
        report(args.report)
    elif args.run:
        run(args.out, args.replicas, args.workers, args.only)
    else:
        ap.error("pass --check-gold, --run, --verify or --report")


if __name__ == "__main__":
    main()
