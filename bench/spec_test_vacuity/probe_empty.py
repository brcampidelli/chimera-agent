"""The eight tasks the generator returned nothing on: what did the provider say, and does a retry fix it?
Registered as an addendum in `PREREGISTRATION.md` before any model call.

Two arms over the same eight tasks and the same re-extracted requirements: `shipped` (one call, no
`max_tokens`, as `generate` was on 2026-09-11) and `retry` (the new `SpecTestGenerator`: explicit
budget, one retry, `finish_reason` recorded). Each reply's `finish_reason`, completion tokens and
length are written down, which is the half the shipped path never did.

    python bench/spec_test_vacuity/probe_empty.py --out bench/spec_test_vacuity/results/<tag>-empty.jsonl
    python bench/spec_test_vacuity/probe_empty.py --report bench/spec_test_vacuity/results/<tag>-empty.jsonl
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "bench" / "local_lift"))

from tasks import TASKS  # noqa: E402  (bench/local_lift)

from chimera.config import get_settings  # noqa: E402
from chimera.core.checklist import RequirementChecklist  # noqa: E402
from chimera.core.spec_test import (  # noqa: E402
    _GEN_SYSTEM,
    SpecTestGenerator,
    _strip_fence,
    workspace_digest,
)
from chimera.orchestration.receipts import price_completion  # noqa: E402
from chimera.providers.gateway import Message  # noqa: E402

#: RESULTS.md, "Ten tasks with no module": the eight that had requirements and got nothing.
EMPTY_TASKS = (
    "fix_collect_items", "fix_percentile", "fix_rotate_list", "fix_merge_settings",
    "fix_insert_pos", "fix_count_words", "fix_first_value", "fix_title_case",
)


@dataclass
class Reply:
    arm: str
    attempt: int
    finish_reason: str
    completion_tokens: int | None
    chars: int
    has_test: bool
    max_tokens: int | None


@dataclass
class Row:
    task_id: str
    requirements: int
    shipped_module: bool
    retry_module: bool
    retry_attempts: int
    retry_finish_reason: str
    replies: list[dict[str, Any]] = field(default_factory=list)
    usd: float | None = None
    seconds: float = 0.0


class _Recording:
    """A backend that keeps every reply's `finish_reason`, tokens and length, and the budget asked."""

    def __init__(self, inner: Any, arm: str) -> None:
        self.inner, self.arm = inner, arm
        self.replies: list[Reply] = []
        self.usd, self.unpriced = 0.0, False

    def complete(self, messages: Any, **kwargs: Any) -> Any:
        result = self.inner.complete(messages, **kwargs)
        cost = price_completion(result)
        self.usd += cost.usd
        self.unpriced = self.unpriced or cost.unpriced is not None
        code = _strip_fence(result.content or "")
        self.replies.append(Reply(
            arm=self.arm, attempt=len(self.replies) + 1,
            finish_reason=str(getattr(result, "finish_reason", "") or ""),
            completion_tokens=getattr(result, "completion_tokens", None), chars=len(code),
            has_test="def test" in code, max_tokens=kwargs.get("max_tokens"),
        ))
        return result


def _materialise(files: dict[str, str], root: Path) -> None:
    for rel, content in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def _shipped_generate(backend: Any, model: str, task: str, requirements: Any, code_context: str) -> str:
    """`SpecTestGenerator.generate` as it was on 2026-09-11: one call, no budget, text or nothing."""
    listing = "\n".join(f"- [{r.kind}] {r.text}" for r in requirements)
    prompt = (
        f"Task:\n{task}\n\nAtomic requirements to test:\n{listing}\n\n"
        f"Code in the workspace:\n{code_context or '(no source files found)'}"
    )
    result = backend.complete(
        [Message(role="system", content=_GEN_SYSTEM), Message(role="user", content=prompt)],
        model=model, temperature=0.0,
    )
    code = _strip_fence(result.content or "")
    return code if "def test" in code else ""


def one(task: dict[str, Any], *, model: str) -> Row:
    from chimera.providers import LLMGateway

    t0 = time.monotonic()
    gateway = LLMGateway()
    extract = _Recording(gateway, "extract")
    shipped = _Recording(gateway, "shipped")
    retry = _Recording(gateway, "retry")
    with tempfile.TemporaryDirectory(prefix="vacuity-empty-") as tmp:
        base = Path(tmp)
        _materialise(task["files"], base)
        digest = workspace_digest(base)
        requirements = RequirementChecklist(extract, model).extract(task["prompt"])
        shipped_code = _shipped_generate(shipped, model, task["prompt"], requirements, digest) if requirements else ""
        gen = SpecTestGenerator(retry, model)
        retry_code = gen.generate(task["prompt"], requirements, code_context=digest)
    usd = extract.usd + shipped.usd + retry.usd
    unpriced = extract.unpriced or shipped.unpriced or retry.unpriced
    return Row(
        task_id=task["id"], requirements=len(requirements), shipped_module=bool(shipped_code),
        retry_module=bool(retry_code), retry_attempts=gen.last_attempts,
        retry_finish_reason=gen.last_finish_reason,
        replies=[asdict(r) for r in shipped.replies + retry.replies],
        usd=(None if unpriced else usd), seconds=round(time.monotonic() - t0, 1),
    )


def report(path: Path) -> str:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    n = len(rows)
    shipped_ok = sum(r["shipped_module"] for r in rows)
    retry_ok = sum(r["retry_module"] for r in rows)
    lines = [
        f"# spec_test_vacuity / probe_empty — {n} tasks, US$ {sum(r['usd'] or 0 for r in rows):.4f}",
        "",
        f"- `shipped` (one call, no budget): module on **{shipped_ok} / {n}**",
        f"- `retry` (explicit budget, one retry): module on **{retry_ok} / {n}**",
        "",
        "| task | reqs | shipped | shipped finish / tokens | retry | attempts | retry finishes / tokens | s |",
        "|---|---:|---|---|---|---:|---|---:|",
    ]
    empties: dict[str, int] = {}
    for r in rows:
        sh = [x for x in r["replies"] if x["arm"] == "shipped"]
        rt = [x for x in r["replies"] if x["arm"] == "retry"]
        for x in sh + rt:
            if not x["has_test"]:
                empties[x["finish_reason"] or "none reported"] = empties.get(x["finish_reason"] or "none reported", 0) + 1
        fmt = lambda xs: ", ".join(f"{x['finish_reason'] or '—'}/{x['completion_tokens'] or '?'}" for x in xs)  # noqa: E731
        lines.append(
            f"| `{r['task_id']}` | {r['requirements']} | {'module' if r['shipped_module'] else '**nothing**'} | {fmt(sh)} "
            f"| {'module' if r['retry_module'] else '**nothing**'} | {r['retry_attempts']} | {fmt(rt)} | {r['seconds']} |"
        )
    lines += ["", "Replies with no test in them, by `finish_reason`: "
              + (", ".join(f"`{k}` × {v}" for k, v in sorted(empties.items(), key=lambda kv: -kv[1])) or "none")]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="")
    ap.add_argument("--report", default="")
    ap.add_argument("--model", default="")
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args()
    if args.report:
        print(report(Path(args.report)))
        return 0
    settings = get_settings()
    if not settings.has_any_key():
        print("no provider key in the environment", file=sys.stderr)
        return 2
    if settings.cache:
        print("CHIMERA_CACHE is on; aborting", file=sys.stderr)
        return 2
    model = args.model or settings.default_model
    tasks = [t for t in TASKS if t["id"] in EMPTY_TASKS]
    out = Path(args.out) if args.out else Path(__file__).with_name("results") / f"{time.strftime('%Y-%m-%d')}-empty.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    done = set()
    if out.exists():
        done = {json.loads(line)["task_id"] for line in out.read_text(encoding="utf-8").splitlines() if line.strip()}
    todo = [t for t in tasks if t["id"] not in done]
    print(f"model {model}; {len(todo)} tasks to run, {len(done)} on disk")
    from concurrent.futures import ThreadPoolExecutor, as_completed

    spent = 0.0
    with out.open("a", encoding="utf-8") as fh, ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(one, t, model=model): t["id"] for t in todo}
        for fut in as_completed(futures):
            tid = futures[fut]
            try:
                r = fut.result()
            except Exception as exc:  # noqa: BLE001 — a provider hiccup must not lose the file
                print(f"  {tid:<20} ERROR {type(exc).__name__}: {str(exc)[:100]}", flush=True)
                continue
            spent += r.usd or 0.0
            fh.write(json.dumps(asdict(r), ensure_ascii=False) + "\n")
            fh.flush()
            print(f"  {tid:<20} shipped={'module' if r.shipped_module else 'NOTHING'} retry={'module' if r.retry_module else 'NOTHING'} "
                  f"attempts={r.retry_attempts} finish={r.retry_finish_reason or '-'} {r.seconds}s  Σ US$ {spent:.4f}", flush=True)
    print(f"written {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
