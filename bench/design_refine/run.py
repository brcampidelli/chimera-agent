"""Design refinement bench (P1-06): what a refinement sends, and what it costs to send less.

    python bench/_with_env.py bench/design_refine/run.py --selftest             # no model, no money
    python bench/_with_env.py bench/design_refine/run.py --gen --model ds       # the ruler, arm A and B
    python bench/_with_env.py bench/design_refine/run.py --corpus --model ds    # the 30 multi-screen designs
    python bench/_with_env.py bench/design_refine/run.py --refine --model ds    # the paired refinement
    python bench/_with_env.py bench/design_refine/run.py --measure --model ds   # contrast and overflow
    python bench/_with_env.py bench/design_refine/run.py --report               # numbers, no money

Arm A is the code of ``BASE_SHA`` byte for byte: its prompt file is read from git, so the baseline
cannot drift with the working tree. Arm B is the working tree, called through the product's own
``propose.propose`` / ``propose.refine``. Both go through the same ``_ask`` machinery, the same
validators and the same dry-run apply. Every call is metered; the spend ledger stops the run at the
registered stop, whatever the phase.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import threading
import time
import types
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO))

from briefs import BRIEFS, Brief, refinement  # noqa: E402
from measure import OverflowMeter, contrast, edit_outcome, first_attempt_edit  # noqa: E402

from chimera.design import propose  # noqa: E402
from chimera.design.ids import new_id  # noqa: E402
from chimera.design.model import DesignDocument, Page, is_index_page, wire  # noqa: E402
from chimera.design.ops import apply_operations  # noqa: E402
from chimera.design.tokens import default_tokens  # noqa: E402

# Amendment 1 (PREREGISTRATION.md): with ``response_format=json_object`` the DeepSeek route returned an empty
# body cut at the token ceiling in 4 of 4 pilot generations, in both arms. Both arms run without it.
propose._json_object_mode = lambda model: {}  # type: ignore[assignment]

BASE_SHA = "b53dd4fe"
#: sha256 of ``chimera/design/prompts.py`` at BASE_SHA, written before any paid call.
BASE_PROMPTS_SHA256 = "8b858d4e7fc3b5cbe05b19d4fab3f6cbf9ef96e26fbaf064a0cc124f71d04629"
MODELS = {
    "ds": "openrouter/deepseek/deepseek-v4-flash-0731",
    "glm": "openrouter/z-ai/glm-5.3-flash",
}
#: Deliberately above every quote seen for each slug (US$ per M tokens in/out; the live index of
#: 2026-11-16 read 0.06/0.18 and 0.04/0.078), cache reads priced as input, so the stop binds early.
#: ``LIVE_PRICES`` is that live index, used only to report an estimate of what was really paid.
CONSERVATIVE_PRICES = {MODELS["ds"]: (0.10, 0.40), MODELS["glm"]: (0.10, 1.30)}
LIVE_PRICES = {MODELS["ds"]: (0.06, 0.18), MODELS["glm"]: (0.0399, 0.0784)}
SPEND_STOP = 4.00
REPS = 3
RESULTS = HERE / "results"
_LEDGER_LOCK = threading.Lock()


# ----------------------------------------------------------------------------------------- baseline
def baseline_prompts() -> types.ModuleType:
    """``chimera/design/prompts.py`` as it was at BASE_SHA, executed as its own module."""
    text = subprocess.run(
        ["git", "show", f"{BASE_SHA}:chimera/design/prompts.py"], cwd=REPO, check=True,
        capture_output=True,
    ).stdout
    if hashlib.sha256(text).hexdigest() != BASE_PROMPTS_SHA256:
        raise SystemExit("the baseline prompt file is not the registered one")
    module = types.ModuleType("baseline_prompts")
    exec(compile(text.decode("utf-8"), "baseline_prompts.py", "exec"), module.__dict__)
    return module


OLD = baseline_prompts()


def baseline_generate_user(doc: DesignDocument, brief: str) -> str:
    return OLD.HTML_GENERATE.format(
        brief=propose._untrusted(brief[: propose.MAX_BRIEF]),
        constraints=propose._untrusted(propose._dumps({})),
        tokens=propose._untrusted(propose._dumps(wire(doc.tokens))),
        assets=propose._untrusted(propose._dumps([])),
        document=propose._untrusted(propose._dumps(propose.summarize(doc))),
        operations=OLD.HTML_OPERATIONS_GUIDE,
    )


def baseline_refine_user(doc: DesignDocument, request: str) -> str:
    return OLD.HTML_REFINE.format(
        request=propose._untrusted(request[: propose.MAX_BRIEF]),
        selected=propose._untrusted(propose._dumps([])),
        comments=propose._untrusted(propose._dumps([])),
        tokens=propose._untrusted(propose._dumps(wire(doc.tokens))),
        document=propose._untrusted(propose._dumps(propose.summarize(doc))),
        html_sources=propose._untrusted(propose.html_sources(doc)),
        html_selection=propose._selected_html(doc, None),
        operations=OLD.HTML_OPERATIONS_GUIDE,
    )


# ------------------------------------------------------------------------------------------ backend
class Meter:
    """One run's calls, tokens and conservative dollars over the shared gateway."""

    _gateway: Any = None
    _gateway_lock = threading.Lock()

    def __init__(self, model: str) -> None:
        self.model = model
        self.calls = self.prompt = self.completion = self.cache_read = self.errors = 0
        self.providers: set[str] = set()

    @classmethod
    def gateway(cls) -> Any:
        with cls._gateway_lock:
            if cls._gateway is None:
                from chimera.providers import LLMGateway

                cls._gateway = LLMGateway()
            return cls._gateway

    def complete(self, messages: Any, **kwargs: Any) -> Any:
        kwargs["model"] = self.model
        try:
            result = self.gateway().complete(messages, **kwargs)
        except Exception:
            self.errors += 1
            raise
        self.calls += 1
        self.prompt += result.prompt_tokens or 0
        self.completion += result.completion_tokens or 0
        self.cache_read += result.cache_read_tokens or 0
        if result.provider:
            self.providers.add(result.provider)
        return result

    @property
    def usd(self) -> float:
        price_in, price_out = CONSERVATIVE_PRICES[self.model]
        return self.prompt / 1e6 * price_in + self.completion / 1e6 * price_out

    @property
    def usd_live(self) -> float:
        price_in, price_out = LIVE_PRICES[self.model]
        return self.prompt / 1e6 * price_in + self.completion / 1e6 * price_out

    def usage(self) -> dict[str, Any]:
        return {"calls": self.calls, "prompt": self.prompt, "completion": self.completion,
                "cache_read": self.cache_read, "errors": self.errors,
                "providers": sorted(self.providers), "usd": round(self.usd, 6),
                "usd_live": round(self.usd_live, 6)}


def spent() -> float:
    path = RESULTS / "spend.json"
    return json.loads(path.read_text(encoding="utf-8"))["usd"] if path.exists() else 0.0


def add_spend(usd: float) -> float:
    with _LEDGER_LOCK:
        RESULTS.mkdir(exist_ok=True)
        total = spent() + usd
        (RESULTS / "spend.json").write_text(json.dumps({"usd": round(total, 6)}), encoding="utf-8")
        return total


def check_budget() -> None:
    if spent() >= SPEND_STOP:
        raise SystemExit(f"spend ledger at US${spent():.4f}, past the registered stop of US${SPEND_STOP:.2f}")


# --------------------------------------------------------------------------------------------- data
def fresh_doc(brief: Brief) -> DesignDocument:
    return DesignDocument(id=f"bench-{brief.id}", title=brief.brief[:100], tokens=default_tokens(),
                          pages=[Page(id="home", name="Home")])


def html_boards(doc: DesignDocument) -> list[Any]:
    return [b for page in doc.pages if not is_index_page(page) for b in page.artboards if b.html is not None]


def _append(path: Path, record: dict[str, Any]) -> None:
    with _LEDGER_LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _arm_order(index: int, rep: int) -> tuple[str, str]:
    return ("A", "B") if (index + rep) % 2 == 0 else ("B", "A")


# --------------------------------------------------------------------------------------- generation
def generate(meter: Meter, doc: DesignDocument, brief_text: str, arm: str) -> propose.ProposalOutcome:
    if arm == "A":
        return propose._proposal_call(meter, doc, baseline_generate_user(doc, brief_text),
                                      model=meter.model, id_factory=new_id)
    return propose.propose(meter, doc, brief_text, {}, model=meter.model)


def run_gen_item(model_key: str, index: int, rep: int) -> None:
    brief = BRIEFS[index]
    out = RESULTS / model_key / "gen.jsonl"
    done = {(r["brief"], r["rep"], r["arm"]) for r in load_jsonl(out)}
    for arm in _arm_order(index, rep):
        if (brief.id, rep, arm) in done:
            continue
        check_budget()
        meter = Meter(MODELS[model_key])
        doc = fresh_doc(brief)
        started = time.time()
        try:
            outcome = generate(meter, doc, brief.brief, arm)
            error, attempts, ok, preview = outcome.error, outcome.attempts, outcome.ok, outcome.preview
        except Exception as exc:  # a provider failure is a halt, kept and counted, never a failed arm
            error, attempts, ok, preview = f"halt: {type(exc).__name__}", 0, False, None
        board = None
        if ok and preview is not None:
            added = html_boards(preview)
            board = wire(added[-1]) if added else None
            ok = board is not None
            error = error or (None if ok else "no html artboard in the proposal")
        add_spend(meter.usd)
        _append(out, {"brief": brief.id, "rep": rep, "arm": arm, "order": list(_arm_order(index, rep)),
                      "ok": ok, "attempts": attempts, "error": error, "board": board,
                      "halt": bool(error and str(error).startswith("halt")),
                      "secs": round(time.time() - started, 1), **meter.usage()})


def cmd_gen(model_key: str) -> None:
    items = [(i, r) for i in range(len(BRIEFS)) for r in range(REPS)]
    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(lambda ir: run_gen_item(model_key, *ir), items))
    print(f"gen done for {model_key}; spent so far US${spent():.4f}")


# ------------------------------------------------------------------------------------------ corpus
def build_design(model_key: str, index: int) -> None:
    brief = BRIEFS[index]
    path = RESULTS / model_key / "corpus" / f"{brief.id}.json"
    if path.exists():
        return
    meter = Meter(MODELS[model_key])
    doc = fresh_doc(brief)
    tries = 0
    for position, name in enumerate(brief.screens):
        request = f"{brief.brief}\n\nDesign the \"{name}\" screen of this product."
        if position:
            request += (" The design already has these screens: "
                        + ", ".join(brief.screens[:position]) + ". Keep this one consistent with them.")
        outcome = None
        for _ in range(4):
            check_budget()
            tries += 1
            try:
                outcome = generate(meter, doc, request, "A")
            except Exception:
                continue
            if outcome.ok and outcome.preview is not None and len(html_boards(outcome.preview)) == position + 1:
                break
        else:
            add_spend(meter.usd)
            _append(RESULTS / model_key / "corpus_failed.jsonl", {"brief": brief.id, "screen": name, "tries": tries})
            return
        preview = outcome.preview
        assert preview is not None
        newest = html_boards(preview)[-1]
        page = next(p for p in preview.pages if any(b.id == newest.id for b in p.artboards))
        doc, _ = apply_operations(preview, [{"type": "artboard.update", "pageId": page.id,
                                            "artboardId": newest.id, "patch": {"name": name}}])
    add_spend(meter.usd)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"brief": brief.id, "tries": tries, "doc": wire(doc), **meter.usage()},
                               ensure_ascii=False), encoding="utf-8")


def cmd_corpus(model_key: str) -> None:
    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(lambda i: build_design(model_key, i), range(len(BRIEFS))))
    made = len(list((RESULTS / model_key / "corpus").glob("*.json")))
    print(f"corpus for {model_key}: {made}/30 designs; spent so far US${spent():.4f}")


def load_design(model_key: str, brief_id: str) -> DesignDocument | None:
    path = RESULTS / model_key / "corpus" / f"{brief_id}.json"
    if not path.exists():
        return None
    return DesignDocument.model_validate(json.loads(path.read_text(encoding="utf-8"))["doc"])


# ------------------------------------------------------------------------------------------ refine
def run_refine_item(model_key: str, index: int, rep: int) -> None:
    brief = BRIEFS[index]
    doc = load_design(model_key, brief.id)
    if doc is None:
        return
    out = RESULTS / model_key / "refine.jsonl"
    done = {(r["brief"], r["rep"], r["arm"]) for r in load_jsonl(out)}
    request, position, how = refinement(index)
    screens = html_boards(doc)
    target = screens[position].id
    for arm in _arm_order(index, rep):
        if (brief.id, rep, arm) in done:
            continue
        check_budget()
        meter = Meter(MODELS[model_key])
        started = time.time()
        try:
            if arm == "A":
                outcome = propose._proposal_call(meter, doc, baseline_refine_user(doc, request),
                                                 model=meter.model, id_factory=new_id)
            else:
                outcome = propose.refine(meter, doc, request, model=meter.model)
            operations = [wire(op) for op in outcome.proposal.operations] if outcome.proposal else []
            ok, attempts, error = outcome.ok, outcome.attempts, outcome.error
        except Exception as exc:
            operations, ok, attempts, error = [], False, 0, f"halt: {type(exc).__name__}"
        add_spend(meter.usd)
        _append(out, {"brief": brief.id, "rep": rep, "arm": arm, "screens": len(screens), "how": how,
                      "target": target, "request": request, "ok": ok, "attempts": attempts, "error": error,
                      "success": first_attempt_edit(ok, attempts, operations, target),
                      "shape": edit_outcome(operations, target),
                      "halt": bool(error and str(error).startswith("halt")),
                      "secs": round(time.time() - started, 1), **meter.usage()})


def cmd_refine(model_key: str) -> None:
    items = [(i, r) for i in range(len(BRIEFS)) for r in range(REPS)]
    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(lambda ir: run_refine_item(model_key, *ir), items))
    print(f"refine done for {model_key}; spent so far US${spent():.4f}")


# ---------------------------------------------------------------------------------------- measuring
def cmd_measure(model_key: str) -> None:
    """Contrast and overflow of every generated artboard. No model; Chromium only; resumable."""
    src, out = RESULTS / model_key / "gen.jsonl", RESULTS / model_key / "gen_measured.jsonl"
    done = {(r["brief"], r["rep"], r["arm"]) for r in load_jsonl(out)}
    tokens = default_tokens()
    with OverflowMeter() as meter:
        for record in load_jsonl(src):
            key = (record["brief"], record["rep"], record["arm"])
            if key in done or not record["ok"] or record["board"] is None:
                continue
            brief = next(b for b in BRIEFS if b.id == record["brief"])
            doc = DesignDocument.model_validate({**wire(fresh_doc(brief)), "pages": [
                {"id": "home", "name": "Home", "artboards": [record["board"]]}]})
            board = html_boards(doc)[0]
            _append(out, {"brief": record["brief"], "rep": record["rep"], "arm": record["arm"],
                          "contrast": contrast(board, tokens), "overflow": meter.overflows(doc, board.id)})
    print(f"measured {model_key}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", choices=sorted(MODELS), default="ds")
    for flag in ("selftest", "gen", "corpus", "refine", "measure", "report"):
        parser.add_argument(f"--{flag}", action="store_true")
    args = parser.parse_args()
    if args.selftest:
        from selftest import run_selftest

        run_selftest()
    elif args.gen:
        cmd_gen(args.model)
    elif args.corpus:
        cmd_corpus(args.model)
    elif args.refine:
        cmd_refine(args.model)
    elif args.measure:
        cmd_measure(args.model)
    elif args.report:
        from report import print_report

        print_report()


if __name__ == "__main__":
    main()
