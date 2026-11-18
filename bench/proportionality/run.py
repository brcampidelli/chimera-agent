"""Run `bench/proportionality` (PREREGISTRATION.md §4). Paid.

One run = one `Agent` in-process, assembled as `chimera agent` assembles it, in a fresh workspace and a
fresh `CHIMERA_HOME`. The only difference between the arms is `AgentConfig.proportionality`.

Resumable: one line per finished run in ``<out>/runs.jsonl``. Stops before starting a pair once the
tally of ``AgentResult.usd`` reaches ``--stop-usd`` (the pre-registered 4.50). The key is read by the
product's settings from the ``.env`` of the working directory and is never printed.

    cd <repo with the .env> && python <worktree>/bench/proportionality/run.py --out <dir> --limit 2
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
MODEL = "openrouter/deepseek/deepseek-chat-v3.1"
SEED = 20261116
MAX_STEPS = 25
GRADER_IMAGE = "chimera-proportionality-pytest:local"
GRADER_TIMEOUT = 60
ADMISSION_USD = 4.50
CEILING_USD = 5.00
#: The five switches whose product default changed on 2026-11-10 (`bench/_legacy_defaults.py`). This
#: bench measures the block on the product AS IT SHIPS, so it does NOT pin them back to the legacy
#: values: the effective value of each is recorded in `switches.json` beside the results instead.
SWITCHES = (
    "CHIMERA_DEFER_TOOLS",
    "CHIMERA_SKILL_CARDS",
    "CHIMERA_CODE_CLOSE_LOOP",
    "CHIMERA_PULL_REQUESTS",
    "CHIMERA_CHAT_INBOUND_MEDIA",
)


def _record_switches(out: Path) -> None:
    from chimera.config import Settings, get_settings

    settings = get_settings()
    found = {}
    for name, field in Settings.model_fields.items():
        if field.validation_alias in SWITCHES:
            found[str(field.validation_alias)] = getattr(settings, name)
    (out / "switches.json").write_text(json.dumps(found, indent=2, sort_keys=True) + chr(10), encoding="utf-8")


def _pairs(replicas: int) -> list[dict]:
    from bench.proportionality import corpus

    rng = random.Random(SEED)
    items = (
        [{"family": "read", "id": it["id"], "item": it} for it in corpus.READ]
        + [{"family": "edit", "id": it["name"], "item": it} for it in corpus.EDIT]
        + [{"family": "control", "id": it["id"], "item": it} for it in corpus.CONTROL]
    )
    pairs = [{**it, "rep": r, "first": rng.choice(["off", "on"])} for it in items for r in range(replicas)]
    rng.shuffle(pairs)
    return pairs


def _materialise(pair: dict, ws: Path) -> None:
    from bench.proportionality import corpus

    fam, item = pair["family"], pair["item"]
    if fam == "read":
        shutil.copytree(corpus.FIXTURE / "src", ws / "src")
    elif fam == "edit":
        for rel, body in item["files"].items():
            path = ws / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(body, encoding="utf-8")


def _prompt(pair: dict) -> str:
    fam, item = pair["family"], pair["item"]
    return item["q"] if fam == "read" else item["prompt"]


def _snapshot(ws: Path) -> dict[str, str]:
    return {p.relative_to(ws).as_posix(): p.read_text(encoding="utf-8", errors="replace")
            for p in ws.rglob("*") if p.is_file() and not p.is_symlink() and not any(q.is_symlink() for q in p.parents if q != ws.parent) and "__pycache__" not in p.parts}


def _grader():
    from chimera.sandbox.docker import DockerSandbox

    class OfflineGrader(DockerSandbox):
        def _argv(self, name: str, command: str, workdir: Path, env_args: list[str]) -> list[str]:
            argv = super()._argv(name, command, workdir, env_args)
            return argv[:2] + ["--pull=never"] + argv[2:]

    return OfflineGrader(image=GRADER_IMAGE, network=False, fallback=None)


def _verdict(pair: dict, ws: Path, answer: str, before: dict[str, str]) -> dict:
    from bench.proportionality import corpus

    fam, item = pair["family"], pair["item"]
    after = _snapshot(ws)
    changed = sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))
    if fam == "read":
        return {"ok": corpus.read_answer_ok(item, answer), "files_changed": len(changed)}
    if fam == "edit":
        # Copy only regular workspace files into a disposable fixture; never mount the home,
        # source checkout or credentials. Symlinks must not lead the host outside the workspace.
        with tempfile.TemporaryDirectory(prefix="prop-grade-") as tmp:
            fixture = Path(tmp)
            for path in ws.rglob("*"):
                if path.is_symlink() or not path.is_file() or any(p.is_symlink() for p in path.parents if p != ws.parent):
                    continue
                dest = fixture / path.relative_to(ws)
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(path, dest)
            test = fixture / item["test"]
            test.parent.mkdir(parents=True, exist_ok=True)
            test.write_text(item["files"][item["test"]], encoding="utf-8")
            sandbox = _grader()
            result = sandbox.run("python -m pytest -q " + shlex.quote(item["test"]), cwd=fixture, timeout=GRADER_TIMEOUT)
            collection_error = (result.exit_code == 2 and "ERROR collecting" in result.output
                                and any(name in result.output for name in ("ImportError", "ModuleNotFoundError", "SyntaxError")))
            if (result.exit_code not in (0, 1) and not collection_error) or result.timed_out:
                raise RuntimeError("isolated grader unavailable or failed; verdict withheld")
            return {"ok": result.exit_code == 0, "files_changed": len(changed)}
    if item["kind"] == "ask":
        return {"ok": ("?" in answer) and not changed, "files_changed": len(changed), "asked": "?" in answer}
    built = "ola mundo" in after.get("hello.py", "")
    return {"ok": built, "files_changed": len(changed)}


def _run_one(pair: dict, arm: str, out: Path, remaining_usd: float) -> dict:
    from chimera.config import get_settings

    previous_home = os.environ.get("CHIMERA_HOME")
    with tempfile.TemporaryDirectory(prefix="prop-home-") as home:
        os.environ["CHIMERA_HOME"] = home
        get_settings.cache_clear()
        try:
            return _run_one_inner(pair, arm, out, remaining_usd)
        finally:
            if previous_home is None:
                os.environ.pop("CHIMERA_HOME", None)
            else:
                os.environ["CHIMERA_HOME"] = previous_home
            get_settings.cache_clear()


class _AmbiguousCall(BaseException):
    """A failed request may have been billed; no retry may leave this process."""


@contextmanager
def _single_attempt_transport():
    # LLMGateway may rotate keys or fallback within complete(), before its caller sees an error.
    # Escape those Exception handlers on a failed transport and disable LiteLLM's own retries.
    import litellm

    complete = litellm.completion

    def once(*args, **kwargs):
        kwargs["num_retries"] = 0
        try:
            return complete(*args, **kwargs)
        except Exception as exc:
            raise _AmbiguousCall() from exc

    with patch.object(litellm, "completion", once):
        yield


class _SingleAttemptBackend:
    def __init__(self, inner):
        self.inner = inner
        self.ambiguous = False

    def complete(self, *args, **kwargs):
        if self.ambiguous:
            raise _AmbiguousCall()
        try:
            return self.inner.complete(*args, **kwargs)
        except (Exception, _AmbiguousCall) as exc:
            self.ambiguous = True
            raise _AmbiguousCall() from exc

    def __getattr__(self, name):
        return getattr(self.inner, name)


def _result_cost_and_error(res, budget) -> tuple[float | None, str]:
    # A timed-out request may still be billed even when the result contains earlier usage.
    if str(res.stopped_reason) == "provider_timeout":
        return None, "provider timeout: cost pending revalidation"
    if res.usd is None or budget.estimated:
        return None, "run cost unknown; refusing to continue"
    cost = max(res.usd, budget.spent)
    if str(res.stopped_reason) == "spend":
        return cost, "spend limit stopped the run before completion"
    return cost, ""


def _run_one_inner(pair: dict, arm: str, out: Path, remaining_usd: float) -> dict:
    from chimera.cli.right_hand import build_right_hand
    from chimera.config import get_settings
    from chimera.core import Agent, AgentConfig
    from chimera.core.agent import attended
    from chimera.orchestration.budget import SpendBudget
    from chimera.providers import LLMGateway

    work = Path(tempfile.mkdtemp(prefix=f"prop-{arm}-{pair['id']}-"))
    ws = work / "ws"
    ws.mkdir()
    _materialise(pair, ws)
    before = _snapshot(ws)
    settings = get_settings()
    hand = build_right_hand(ws, settings=settings, surface="agent", allow=None, deny=None, mode="")
    hand.begin_turn(_prompt(pair))
    cfg = attended(AgentConfig(model=MODEL, max_steps=MAX_STEPS, project_root=ws, turn_context=True,
                               proportionality=(arm == "on"), max_usd=remaining_usd))
    started = time.time()
    error = ""
    try:
        budget = SpendBudget(remaining_usd, strict=True)
        backend = _SingleAttemptBackend(LLMGateway())
        with _single_attempt_transport():
            res = Agent(backend, hand.registry, cfg).run(_prompt(pair), spend=budget)
        cost, error = _result_cost_and_error(res, budget)
        row = {"steps": res.steps, "completion_tokens": res.completion_tokens, "prompt_tokens": res.prompt_tokens,
               "tool_calls": res.tool_calls_made, "tools": res.tool_names, "usd": cost, "cost_known": cost is not None,
               "stopped": str(res.stopped_reason), "answer": str(res.answer)[:1200]}
    except _AmbiguousCall:
        error = "provider attempt failed: cost pending revalidation"
        row = {"steps": 0, "completion_tokens": 0, "prompt_tokens": 0, "tool_calls": 0, "tools": [],
               "usd": None, "cost_known": False, "stopped": "error", "answer": ""}
    except Exception as exc:  # a run that errors is recorded, never retried: no re-rolls
        error = f"{type(exc).__name__}: {exc}"[:300]
        row = {"steps": 0, "completion_tokens": 0, "prompt_tokens": 0, "tool_calls": 0, "tools": [],
               "usd": None, "cost_known": False, "stopped": "error", "answer": ""}
    try:
        verdict = (_verdict(pair, ws, row["answer"], before) if not error
                   else {"ok": False, "files_changed": 0})
    except Exception as exc:
        error = error or f"grader: {type(exc).__name__}: {exc}"[:300]
        verdict = {"ok": False, "files_changed": 0}
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return {"family": pair["family"], "id": pair["id"], "rep": pair["rep"], "arm": arm, **row, **verdict,
            "error": error, "seconds": round(time.time() - started, 1)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--replicas", type=int, default=3)
    ap.add_argument("--limit", type=int, default=0, help="only the first N pairs of the shuffled order")
    ap.add_argument("--stop-usd", type=float, default=4.5)
    args = ap.parse_args()
    if not math.isfinite(args.stop_usd) or not 0 < args.stop_usd <= ADMISSION_USD:
        ap.error("--stop-usd must be positive and no higher than the registered US$ 4.50")

    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(HERE.parents[1]))
    from chimera.core.filelock import try_hold

    lock = try_hold(out / "runner")
    if lock is None or not lock.real:
        raise SystemExit("output directory is locked or cannot be locked; no runs started")
    try:
        return _main_locked(args, out)
    finally:
        lock.release()


def _main_locked(args, out: Path) -> int:
    log = out / "runs.jsonl"
    marker = out / "inflight.json"
    if marker.exists():
        raise SystemExit("interrupted run has an unresolved in-flight cost; revalidate before resuming")
    rows = [json.loads(x) for x in log.read_text(encoding="utf-8").splitlines() if x.strip()] if log.exists() else []
    if any(not isinstance(r.get("usd"), (int, float)) or isinstance(r.get("usd"), bool)
           or not math.isfinite(r["usd"]) or r["usd"] < 0
           or (r.get("error") and r["usd"] == 0 and not r.get("cost_known", False)) for r in rows):
        raise SystemExit("unknown or ambiguous recorded cost; revalidate before resuming")

    # Check before any paid call. The grader image must already contain pytest; it is never pulled.
    if not _grader().available():
        raise SystemExit("Docker unavailable; isolated grader required")
    try:
        image = subprocess.run(["docker", "image", "inspect", GRADER_IMAGE], capture_output=True,
                               timeout=10, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        raise SystemExit("grader image inspection failed") from exc
    if image.returncode:
        raise SystemExit(f"local grader image {GRADER_IMAGE} is required; no image will be pulled")
    with tempfile.TemporaryDirectory(prefix="prop-grader-check-") as empty:
        check = _grader().run("python -m pytest --version", cwd=Path(empty), timeout=GRADER_TIMEOUT)
        if check.exit_code != 0 or check.timed_out:
            raise SystemExit("isolated grader image cannot run pytest; no model call was made")
    with tempfile.TemporaryDirectory(prefix="prop-settings-") as settings_home:
        from chimera.config import get_settings

        old_home = os.environ.get("CHIMERA_HOME")
        os.environ["CHIMERA_HOME"] = settings_home
        get_settings.cache_clear()
        try:
            _record_switches(out)
        finally:
            if old_home is None:
                os.environ.pop("CHIMERA_HOME", None)
            else:
                os.environ["CHIMERA_HOME"] = old_home
            get_settings.cache_clear()
    done = {(r["family"], r["id"], r["rep"], r["arm"]) for r in rows}
    spent = sum(r["usd"] for r in rows)
    pairs = _pairs(args.replicas)
    if args.limit:
        pairs = pairs[: args.limit]
    # The first four complete pairs determine the maximum prefix of shuffled pairs.
    # Recompute on resume from persisted rows, never from a selective family subset.
    first = pairs[:4]
    first_keys = {(p["family"], p["id"], p["rep"], arm) for p in first for arm in ("off", "on")}
    for index, pair in enumerate(pairs):
        first_rows = [r for r in rows if (r["family"], r["id"], r["rep"], r["arm"]) in first_keys]
        order = [pair["first"], "on" if pair["first"] == "off" else "off"]
        pair_done = [(pair["family"], pair["id"], pair["rep"], arm) in done for arm in order]
        if all(pair_done):
            continue
        if not any(pair_done) and len(first_keys) == 8 and len(first_rows) == 8:
            pilot_cost = sum(r["usd"] for r in first_rows)
            if pilot_cost > 0 and index >= max(4, int(CEILING_USD * 4 / pilot_cost)):
                print("projection stop: first four pairs project beyond US$ 5.00")
                break
        # A partially recorded pair was already admitted; finish it below the absolute ceiling.
        if not any(pair_done) and spent >= args.stop_usd:
            print(f"admission stop: US$ {spent:.4f} spent")
            break
        # Reserve for the second arm using half the pilot's mean pair cost, with the registered
        # $0.50 admission headroom split evenly until the pilot exists. This is a forecast, not a
        # guarantee that an unpredictable second arm can finish within the absolute ceiling.
        pilot_cost = sum(r["usd"] for r in first_rows) if len(first_keys) == 8 and len(first_rows) == 8 else None
        reserve = pilot_cost / 8 if pilot_cost is not None else (CEILING_USD - ADMISSION_USD) / 2
        if not any(pair_done) and CEILING_USD - spent <= 2 * reserve:
            print("pair admission stop: projected pair does not fit below the ceiling")
            break
        for arm in order:
            if (pair["family"], pair["id"], pair["rep"], arm) in done:
                continue
            second_pending = not any(pair_done) and arm == order[0]
            remaining = CEILING_USD - spent - (reserve if second_pending else 0)
            if remaining <= 0:
                print("admission stop: no budget remains for the second arm")
                break
            # A crash anywhere after this durable marker blocks automatic resumption.
            with marker.open("x", encoding="utf-8") as fh:
                json.dump({"family": pair["family"], "id": pair["id"], "rep": pair["rep"], "arm": arm}, fh)
                fh.flush()
                os.fsync(fh.fileno())
            row = _run_one(pair, arm, out, remaining)
            with log.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
                fh.flush()
                os.fsync(fh.fileno())
            if row["usd"] is not None:
                marker.unlink()
            rows.append(row)
            if row["usd"] is not None:
                spent += row["usd"]
            if row["error"]:
                print("run or grader failed; stopping because the cost or verdict may be unknown")
                return 1
            print(f"{pair['family']:7} {pair['id']:30} r{pair['rep']} {arm:3} ok={row['ok']!s:5} steps={row['steps']:<3} "
                  f"out={row['completion_tokens']:<5} US$ {spent:.4f} {row['error']}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
