"""H11 — a typed three-state verdict against the shipped cautious judge, out of sample.

Read `PREREGISTRATION-h11.md` first. The items, the two arms, the frozen prompt, the two operating
points, the metrics, the replay floor, the adoption rule, the budget and the stop rule were fixed
there before this file called a model.

    python bench/review_judge/run_h11.py --dry-run                        # every prompt, no calls
    python bench/review_judge/run_h11.py --pilot --out DIR                # 20 in-sample items, A and T
    python bench/review_judge/run_h11.py --run --out DIR --pilot-dir P    # the 814, resumable blocks
    python bench/review_judge/read_h11_three.py DIR                       # the registered read

What this adds to `run_judge.py`, which it imports and does not change:

- **an out-of-sample-only selector** — the 814 graded rows the August arms saw that were not in the
  pilot draw arm C was written against, checked against those rows' own keys;
- **a three-state parser** — `confirmed | plausible | refuted` plus the quoted line, with `unparsed`
  kept apart from `call_failed`;
- **the provider pin in the manifest** — every call goes to one OpenRouter provider with fallbacks off,
  and every row records the route that answered.

Arm A goes through `run_judge.ask` itself, with `run_judge.system_prompt("cautious")`, so its prompt,
user message and verdict parser are the published ones byte for byte (`--dry-run` checks both).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import sys
import threading
import time
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import asdict
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import run_judge as rj  # noqa: E402

MODEL = "openrouter/deepseek/deepseek-r1"
#: The one provider OpenRouter lists for this model on 2026-09-25 (`/api/v1/models/deepseek/deepseek-r1/
#: endpoints`: Novita, fp8, US$ 0.70/M prompt, US$ 2.50/M completion, 16k completion ceiling).
PROVIDER = "Novita"
PRICE_PROMPT = 0.70e-6
PRICE_COMPLETION = 2.50e-6
SEED = rj.SEED
REPLAY_N = 200
PILOT_N = 20
CAP_USD = 9.00
STOP_USD = 8.75
MIN_ROWS_FOR_STOP_RULE = 100
#: The published arm A's `prompt_sha` (results/cautious/manifest.json). Arm A here must be that prompt.
A_PROMPT_SHA = "60a2749ed8c9"
N_OUT_OF_SAMPLE = 814
STATES = ("confirmed", "plausible", "refuted")

# --- the three-state arm: frozen in PREREGISTRATION-h11.md --------------------------------------
#
# The header is arm A's (`run_judge._HEAD`), byte for byte. The refuted grounds are arm A's two
# rejection grounds, word for word. What changes is the answer: three states and a quoted line
# instead of approve/reject, and so arm A's stance paragraph goes — its last sentence ("When your
# evidence falls short, APPROVE") is the rule for the uncertain middle, and here the middle has a
# state of its own. The cost asymmetry that paragraph argued for is carried by the operating point
# chosen after the call, not by the wording.
_THREE_STATE = (
    "Give one of three verdicts:\n"
    "  - confirmed: a line of the diff shows that the defect the comment reports is there;\n"
    "  - refuted: the code the comment describes is not in this diff, or a line of the diff "
    "contradicts its central claim;\n"
    "  - plausible: neither of those — the diff does not show the defect, and nothing in it "
    "contradicts the comment.\n\n"
    "Quote the one line of the diff your verdict rests on, copied exactly as it appears. Leave the "
    "quote empty only when the code the comment describes is not in this diff.\n\n"
    "Answer with JSON and nothing else, with the keys in this order:\n"
    '{"quote": "<one line of the diff, verbatim>", "reason": "<one line, the evidence>", '
    '"verdict": "confirmed" | "plausible" | "refuted"}'
)


def three_state_prompt() -> str:
    return rj._HEAD + _THREE_STATE


def a_prompt() -> str:
    return rj.system_prompt("cautious")


def sha12(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:12]


def user_message(item: rj.Item) -> str:
    """The user turn, as `run_judge.ask` builds it. `--dry-run` proves the two are identical."""
    return (
        f"File: {item.path}\n"
        f"The comment is attached to lines {item.from_line}-{item.to_line} of the new file.\n\n"
        f"--- diff ---\n{item.patch}\n--- end diff ---\n\n"
        f"Review comment:\n{item.note}\n"
    )


def item_key(item: rj.Item) -> tuple[Any, ...]:
    """`row_id` is 0 on every row (the dataset has no `__index__`); an item is its content."""
    return (item.repo, item.pr, item.path, item.from_line, item.to_line, item.note, item.label)


def item_id(item: rj.Item) -> str:
    return hashlib.sha1(json.dumps(item_key(item), ensure_ascii=False).encode()).hexdigest()[:16]


# --- the parser ----------------------------------------------------------------------------------

_VERDICT = re.compile(r'"verdict"\s*:\s*"(confirmed|plausible|refuted)"', re.I)
_QUOTE = re.compile(r'"quote"\s*:\s*"((?:[^"\\]|\\.)*)"', re.S)
_REASON = re.compile(r'"reason"\s*:\s*"((?:[^"\\]|\\.)*)"', re.S)
_BARE = re.compile(r"\b(confirmed|plausible|refuted)\b", re.I)


def _unescape(raw: str) -> str:
    try:
        return str(json.loads(f'"{raw}"'))
    except json.JSONDecodeError:
        return raw


def parse_three(text: str) -> dict[str, str]:
    """Read one three-state answer. `state` is one of STATES, or `unparsed`.

    Three ways in, in order, and the way that worked is kept in `parse`:
      1. `json` — the reply's outermost object loads and its verdict is one of the three;
      2. `pattern` — it does not load (a quoted code line with an unescaped `"` is the usual cause)
         but a `"verdict": "<state>"` pair is there;
      3. `bare` — no verdict key, and exactly ONE of the three words appears in the whole reply.
         The reason line uses "confirmed" or "plausible" in passing often enough that taking the
         first of several would be a guess, so several distinct words is `unparsed`.
    `unparsed` is a model answer the parser could not read — data about the judge. It never shares
    a bucket with `call_failed`, which is a call that did not complete.
    """
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        try:
            obj = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            obj = None
        if isinstance(obj, dict):
            state = str(obj.get("verdict", "")).strip().lower()
            if state in STATES:
                return {"state": state, "quote": str(obj.get("quote") or ""),
                        "reason": str(obj.get("reason") or ""), "parse": "json"}
    match = _VERDICT.search(text)
    if match:
        quote, reason = _QUOTE.search(text), _REASON.search(text)
        return {"state": match.group(1).lower(), "quote": _unescape(quote.group(1)) if quote else "",
                "reason": _unescape(reason.group(1)) if reason else "", "parse": "pattern"}
    words = {w.lower() for w in _BARE.findall(text)}
    if len(words) == 1:
        return {"state": words.pop(), "quote": "", "reason": text, "parse": "bare"}
    return {"state": "unparsed", "quote": "", "reason": text, "parse": "none"}


def _norm(text: str) -> str:
    return " ".join(text.split())


def quote_in_diff(quote: str, patch: str) -> bool | None:
    """Is the quoted line in the diff the judge was shown? None when the quote is empty.

    Whitespace is collapsed and a leading diff marker (`+`, `-`, space) is dropped on both sides, so
    a quote copied with or without its marker matches. A part of one line counts; a line the model
    re-typed with a change does not. Exploratory only (PREREGISTRATION-h11.md, secondary S3).
    """
    q = quote.strip()
    if q[:1] in ("+", "-"):
        q = q[1:]
    q = _norm(q)
    if not q:
        return None
    lines = [_norm(line[1:] if line[:1] in ("+", "-", " ") else line)
             for line in patch.splitlines() if not line.startswith("@@")]
    return any(q in line for line in lines)


def _selftest() -> None:
    """The parser on shapes it must read and shapes it must refuse."""
    cases = [
        ('{"quote": "+  return x;", "reason": "r", "verdict": "confirmed"}', "confirmed", "json"),
        ('```json\n{"quote": "", "reason": "absent", "verdict": "refuted"}\n```', "refuted", "json"),
        ('{"quote": "if (a == "b") {", "reason": "r", "verdict": "plausible"}', "plausible", "pattern"),
        ('{"quote": "x", "reason": "r", "verdict": "Refuted"}', "refuted", "json"),
        ("Verdict: plausible", "plausible", "bare"),
        ("It is plausible, not confirmed.", "unparsed", "none"),
        ('{"quote": "x", "reason": "r", "verdict": "approve"}', "unparsed", "none"),
        ("", "unparsed", "none"),
    ]
    for text, state, how in cases:
        got = parse_three(text)
        assert (got["state"], got["parse"]) == (state, how), (text, got)
    assert parse_three('{"quote": "if (a == \\"b\\")", "reason": "r", "verdict": "confirmed"}')["quote"] == 'if (a == "b")'
    assert quote_in_diff("+  return x;", "@@ -1 +1 @@\n-  return y;\n+  return x;") is True
    assert quote_in_diff("return   x;", "@@ -1 +1 @@\n+  return x;") is True
    assert quote_in_diff("return z;", "@@ -1 +1 @@\n+  return x;") is False
    assert quote_in_diff("", "+x") is None


# --- the items -----------------------------------------------------------------------------------


def out_of_sample() -> list[rj.Item]:
    """The 814, in a fixed seeded order; the first REPLAY_N of that order carry the replay arm.

    Checked against the August arm A's own rows, so "out of sample" means exactly the rows that
    `RESULTS.md` read as out of sample and nothing the selector happens to reproduce."""
    usable, _dropped = rj.attach_patches(rj.everything(rj.load_rows()))
    items = [i for i in usable if not i.in_pilot]
    august = [json.loads(x) for x in (rj.OUT / "cautious" / "details.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
    theirs = {(r["repo"], r["pr"], r["path"], r["from_line"], r["to_line"], r["note"], r["label"])
              for r in august if not r["in_pilot"]}
    mine = {item_key(i) for i in items}
    if len(items) != N_OUT_OF_SAMPLE or mine != theirs or len({item_id(i) for i in items}) != len(items):
        sys.exit(f"SELECTOR FAILED: {len(items)} items, equal to August's out-of-sample keys: {mine == theirs}")
    random.Random(SEED).shuffle(items)
    return items


def pilot_items() -> list[rj.Item]:
    """20 of the pilot's 105 in-sample items, 10 per label, seeded. Never part of the main read."""
    usable, _dropped = rj.attach_patches(rj.everything(rj.load_rows()))
    ins = [i for i in usable if i.in_pilot]
    rng = random.Random(SEED + 1)
    good = rng.sample([i for i in ins if i.label == 1], PILOT_N // 2)
    bad = rng.sample([i for i in ins if i.label == 0], PILOT_N // 2)
    picked = good + bad
    rng.shuffle(picked)
    return picked


# --- the calls -----------------------------------------------------------------------------------


class _Pinned:
    """The gateway, every call pinned to one OpenRouter provider with fallbacks off.

    The last reply is kept per thread, so `run_judge.ask` — which returns only the verdict — can be
    used unchanged for arm A while the row still records the route, the finish reason and the raw
    answer. Each arm call runs on its own thread.
    """

    def __init__(self) -> None:
        from chimera.providers.gateway import LLMGateway

        self.gateway = LLMGateway()
        self.local = threading.local()

    def complete(self, messages: Any, **kwargs: Any) -> Any:
        kwargs["extra_body"] = {"provider": {"order": [PROVIDER], "allow_fallbacks": False}}
        reply = self.gateway.complete(messages, **kwargs)
        self.local.last = reply
        return reply


def usd(prompt: int, completion: int) -> float:
    return prompt * PRICE_PROMPT + completion * PRICE_COMPLETION


def call_arm(backend: _Pinned, arm: str, item: rj.Item) -> dict[str, Any]:
    """One arm's call on one item. A failed CALL is retried; an unreadable ANSWER is not — it is data."""
    last: Exception | None = None
    for attempt in range(3):
        backend.local.last = None
        started = time.time()
        try:
            if arm in ("A", "A2"):
                verdict, reason, _against, _usage = rj.ask(backend, item, MODEL, "cautious")
                reply = backend.local.last
                parsed: dict[str, Any] = {"verdict": verdict, "reason": reason}
            else:
                reply = backend.complete(
                    [{"role": "system", "content": three_state_prompt()},
                     {"role": "user", "content": user_message(item)}],
                    model=MODEL, temperature=0.0,
                )
                got = parse_three((getattr(reply, "content", None) or "").strip())
                parsed = {"verdict": got["state"], "reason": got["reason"], "quote": got["quote"],
                          "parse": got["parse"], "quote_in_diff": quote_in_diff(got["quote"], item.patch)}
            prompt = int(getattr(reply, "prompt_tokens", 0) or 0)
            completion = int(getattr(reply, "completion_tokens", 0) or 0)
            return {
                "arm": arm, **parsed,
                "raw": (getattr(reply, "content", None) or "").strip(),
                "provider": str(getattr(reply, "provider", "") or ""),
                "finish_reason": str(getattr(reply, "finish_reason", "") or ""),
                "truncated": bool(getattr(reply, "truncated", False)),
                "generation_id": str(getattr(reply, "generation_id", "") or ""),
                "prompt": prompt, "completion": completion, "usd": usd(prompt, completion),
                "attempts": attempt + 1, "started": round(started, 1),
                "seconds": round(time.time() - started, 1),
            }
        except Exception as exc:  # noqa: BLE001 — transport, not judgement
            last = exc
            if attempt < 2:
                time.sleep(20 * (attempt + 1))
    return {"arm": arm, "verdict": "call_failed", "reason": f"call failed after 3 attempts: {last}"[:600],
            "raw": "", "provider": "", "attempts": 3, "prompt": 0, "completion": 0, "usd": 0.0}


def grade_item(backend: _Pinned, item: rj.Item, order: int, replay: bool) -> dict[str, Any]:
    """Every arm of one item, launched together so they share the same minutes and route."""
    arms = ["A", "T"] + (["A2"] if replay else [])
    with ThreadPoolExecutor(max_workers=len(arms)) as pool:
        futures = {arm: pool.submit(call_arm, backend, arm, item) for arm in arms}
        calls = {arm: f.result() for arm, f in futures.items()}
    fields = {k: v for k, v in asdict(item).items() if k != "patch"}
    return {"id": item_id(item), "order": order, "replay": replay, "patch_sha": sha12(item.patch),
            **fields, "arms": calls}


# --- the driver ----------------------------------------------------------------------------------


def _read_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def _spent(rows: list[dict[str, Any]]) -> float:
    return sum(call.get("usd", 0.0) for row in rows for call in row["arms"].values())


def _stop_reason(rows: list[dict[str, Any]]) -> str | None:
    """PREREGISTRATION-h11.md, stop rule (3) and (4)."""
    for row in rows:
        for arm, call in row["arms"].items():
            served = call.get("provider", "")
            if served and served.lower() != PROVIDER.lower():
                return f"pin broken: {arm} on {row['id']} served by {served!r}"
    if len(rows) < MIN_ROWS_FOR_STOP_RULE:
        return None
    for arm in ("A", "T"):
        verdicts = [row["arms"][arm]["verdict"] for row in rows]
        failed = sum(v == "call_failed" for v in verdicts) / len(verdicts)
        unparsed = sum(v == "unparsed" for v in verdicts) / len(verdicts)
        if failed > 0.10:
            return f"arm {arm}: call_failed {failed:.1%} > 10% after {len(rows)} items"
        if unparsed > 0.10:
            return f"arm {arm}: unparsed {unparsed:.1%} > 10% after {len(rows)} items"
    return None


def _manifest(out: Path, mode: str, block: dict[str, Any]) -> None:
    from importlib.metadata import version

    from chimera.config import get_settings

    settings = get_settings()
    path = out / "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"blocks": []}
    manifest.update({
        "mode": mode,
        "judge_model": MODEL,
        "provider_pin": {"order": [PROVIDER], "allow_fallbacks": False, "how": "extra_body on every call"},
        "price_usd_per_token": {"prompt": PRICE_PROMPT, "completion": PRICE_COMPLETION},
        "temperature": 0.0,
        "thinking": "not passed — the model's default reasoning, as in the August runs",
        "seed": SEED,
        "window_lines": rj.WINDOW,
        "replay_n": REPLAY_N if mode == "run" else 0,
        "prompt_sha": {"A": sha12(a_prompt()), "T": sha12(three_state_prompt())},
        "a_prompt_sha_published": A_PROMPT_SHA,
        "settings": {
            "cache": settings.cache, "prompt_cache": settings.prompt_cache,
            "completion_ceiling": settings.completion_ceiling,
            "request_timeout": settings.request_timeout,
            "provider_order_env": settings.provider_order,
            "fallback_models": list(settings.fallback_models),
        },
        "chimera_version": version("chimera-agent"),
        "litellm_version": version("litellm"),
    })
    manifest["blocks"].append(block)
    path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")


def drive(out: Path, items: list[rj.Item], replay_ids: set[str], *, workers: int, deadline: float,
          prior_usd: float, mode: str) -> None:
    """Resumable: rows already in `rows.jsonl` are skipped; one line per item, flushed as it lands.

    New items stop being submitted at `deadline` seconds, at the budget stop, or on a stop rule;
    items in flight are then waited for, so a block ends with nothing paid for and unrecorded unless
    the outer `timeout` has to cut a call that ran far past its peers.
    """
    out.mkdir(parents=True, exist_ok=True)
    if (out / "STOP").exists():
        sys.exit(f"refusing to run: {out / 'STOP'} says {(out / 'STOP').read_text(encoding='utf-8')!r}")
    rows_path = out / "rows.jsonl"
    rows = _read_rows(rows_path)
    done = {row["id"] for row in rows}
    order = {item_id(item): n for n, item in enumerate(items)}
    todo = [item for item in items if item_id(item) not in done]
    spent = prior_usd + _spent(rows)
    per_item = (_spent(rows) / len(rows)) if rows else 0.0
    print(f"[{mode}] {len(rows)} rows on disk, {len(todo)} to go · spent so far US$ {spent:.3f} "
          f"(pilot US$ {prior_usd:.3f}) · workers {workers} · deadline {deadline:.0f}s", flush=True)

    backend = _Pinned()
    started = time.time()
    in_flight: dict[Future[dict[str, Any]], rj.Item] = {}
    queue = iter(todo)
    stop: str | None = _stop_reason(rows) if mode == "run" else None
    wrote = 0
    with ThreadPoolExecutor(max_workers=workers) as pool, rows_path.open("a", encoding="utf-8") as fh:
        while True:
            while stop is None and len(in_flight) < workers and time.time() - started < deadline:
                guess = per_item or 0.012
                if spent + guess * (len(in_flight) + 1) >= STOP_USD:
                    stop = f"budget: US$ {spent:.3f} spent, stop at US$ {STOP_USD:.2f}"
                    break
                item = next(queue, None)
                if item is None:
                    break
                iid = item_id(item)
                in_flight[pool.submit(grade_item, backend, item, order[iid], iid in replay_ids)] = item
            if not in_flight:
                break
            finished, _ = wait(in_flight, return_when=FIRST_COMPLETED)
            for future in finished:
                in_flight.pop(future)
                row = future.result()
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
                fh.flush()
                rows.append(row)
                wrote += 1
                cost = sum(c.get("usd", 0.0) for c in row["arms"].values())
                spent += cost
                per_item = _spent(rows) / len(rows)
                marks = " ".join(f"{arm}={c['verdict']}" for arm, c in row["arms"].items())
                print(f"  {len(rows):>4} label={row['label']} {marks:<44} US$ {cost:.4f} "
                      f"total US$ {spent:.3f} · {time.time() - started:6.0f}s", flush=True)
                if mode == "run" and stop is None:
                    stop = _stop_reason(rows)
    if stop and not stop.startswith("budget"):
        (out / "STOP").write_text(stop, encoding="utf-8")
    block = {"started": round(started, 1), "seconds": round(time.time() - started, 1), "wrote": wrote,
             "rows_after": len(rows), "usd_after": round(spent, 4), "stop": stop, "workers": workers}
    _manifest(out, mode, block)
    remaining = len(items) - len(rows)
    projected = spent + remaining * per_item
    print(f"[{mode}] block done: wrote {wrote}, {len(rows)}/{len(items)} rows, US$ {spent:.3f} spent, "
          f"projected total US$ {projected:.2f} · stop: {stop}", flush=True)


def pilot_cost(pilot_dir: Path) -> tuple[float, float, float]:
    """(pilot spend, mean US$ per A call, mean US$ per T call) from the pilot's rows."""
    rows = _read_rows(pilot_dir / "rows.jsonl")
    if not rows:
        sys.exit(f"no pilot rows in {pilot_dir}")
    a = [r["arms"]["A"]["usd"] for r in rows if r["arms"]["A"]["verdict"] != "call_failed"]
    t = [r["arms"]["T"]["usd"] for r in rows if r["arms"]["T"]["verdict"] != "call_failed"]
    return _spent(rows), sum(a) / len(a), sum(t) / len(t)


def dry_run() -> None:
    _selftest()
    print("parser self-test: ok")
    a, t = a_prompt(), three_state_prompt()
    print(f"arm A prompt sha {sha12(a)} (published {A_PROMPT_SHA}) "
          f"{'ok' if sha12(a) == A_PROMPT_SHA else 'MISMATCH'}")
    if sha12(a) != A_PROMPT_SHA:
        sys.exit("arm A is not the published prompt")
    print(f"arm T prompt sha {sha12(t)}\n\n--- arm T system prompt ---\n{t}\n--- end ---\n")

    class _Capture:
        messages: list[dict[str, str]] = []

        def complete(self, messages: Any, **_kwargs: Any) -> Any:
            _Capture.messages = messages
            return type("R", (), {"content": '{"reason": "x", "verdict": "approve"}'})()

    main, pilot = out_of_sample(), pilot_items()
    for item in main + pilot:
        rj.ask(_Capture(), item, MODEL, "cautious")
        sent = _Capture.messages
        assert sent[0]["content"] == a and sent[1]["content"] == user_message(item), item_key(item)
    print(f"user message identical to run_judge.ask's on all {len(main) + len(pilot)} items: ok")
    good = sum(i.label == 1 for i in main)
    print(f"main: {len(main)} out-of-sample items ({good} correct, {len(main) - good} incorrect); "
          f"replay on the first {REPLAY_N} of the seeded order "
          f"({sum(i.label == 1 for i in main[:REPLAY_N])} correct)")
    print(f"pilot: {len(pilot)} in-sample items ({sum(i.label == 1 for i in pilot)} correct), "
          f"none in the main set: {not ({item_id(i) for i in pilot} & {item_id(i) for i in main})}")
    sizes = sorted(len(user_message(i)) for i in main)
    print(f"user message chars: min {sizes[0]} / median {sizes[len(sizes) // 2]} / max {sizes[-1]}")


def main() -> None:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--pilot", action="store_true")
    mode.add_argument("--run", action="store_true")
    parser.add_argument("--out", type=Path)
    parser.add_argument("--pilot-dir", type=Path, help="The pilot's output, for the cost projection.")
    parser.add_argument("--workers", type=int, default=8, help="Items in flight; each runs its arms together.")
    parser.add_argument("--deadline", type=float, default=2300.0,
                        help="Seconds after which no new item starts (the block runs under timeout 3000).")
    args = parser.parse_args()

    if args.dry_run:
        dry_run()
        return
    if args.out is None:
        sys.exit("--out is required")
    if args.pilot:
        drive(args.out, pilot_items(), set(), workers=args.workers, deadline=args.deadline,
              prior_usd=0.0, mode="pilot")
        return

    if args.pilot_dir is None:
        sys.exit("--pilot-dir is required: the projection that gates the main run reads it")
    items = out_of_sample()
    replay_ids = {item_id(i) for i in items[:REPLAY_N]}
    prior, cost_a, cost_t = pilot_cost(args.pilot_dir)
    projected = prior + len(items) * (cost_a + cost_t) + REPLAY_N * cost_a
    print(f"projection from the pilot: US$ {prior:.3f} pilot + {len(items)} x (A {cost_a:.5f} + "
          f"T {cost_t:.5f}) + {REPLAY_N} x A = US$ {projected:.2f} against a cap of US$ {CAP_USD:.2f}")
    if projected > CAP_USD:
        sys.exit("ABORT: the projected cost exceeds the cap (PREREGISTRATION-h11.md, stop rule 1)")
    drive(args.out, items, replay_ids, workers=args.workers, deadline=args.deadline,
          prior_usd=prior, mode="run")


if __name__ == "__main__":
    main()
