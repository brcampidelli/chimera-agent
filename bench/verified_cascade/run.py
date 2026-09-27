"""The verified-cascade harness: stages S0–S6 of PREREGISTRATION.md §9, with their gates.

    python bench/verified_cascade/run.py --dry-run --out DIR            # every stage, fake backend, no network
    python bench/verified_cascade/run.py --stage s1 --out DIR           # one stage of the paid run

Every call is appended to ``DIR/calls.jsonl`` as it completes (the request body without the key, the
billed-or-computed cost, the served provider), so a stopped stage resumes where it stopped: a call
that succeeded, or halted after its one fresh re-run, is never made again. Spend is the sum of that
log; a paid call is admitted only while spend plus its estimated cost stays under US$ 18.00
(Amendment 0), and the stage stops cleanly when it would not. Each stage writes its gate to
``DIR/gates.json``; a stage refuses to start while an earlier one is missing or failed.

Human adjudications of grader disagreements go in ``DIR/adjudications.jsonl``
(``{"target": "<item_id>|<draw>", "label": ...}``); the queue is ``DIR/adjudication_queue.jsonl``.
In ``--dry-run`` the queue is answered with G1's vote and marked as such.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from bench.verified_cascade.backends import Backends, FakeBackends, request_spec  # noqa: E402
from bench.verified_cascade.common import number_check_fires, sha  # noqa: E402
from bench.verified_cascade.harness import (  # noqa: E402
    ADMISSION_STOP_USD,
    DRAFT_SYSTEM_SHA,
    GRADER_SYSTEM,
    PINS,
    THRESHOLD,
    BudgetExhausted,
    Ledger,
    decision_state,
    draft_messages,
    grader_user,
    permuted,
    run_call,
    wall_violations,
)
from bench.verified_cascade.replay import (  # noqa: E402
    DRAFT_MODEL,
    GRADERS,
    RunData,
    draft_key,
    escalated_from_d1,
    grade_key,
    load_vslice,
    outcomes,
    read_key,
    write_json,
)

STAGES = ("s0", "s1", "s2", "s3", "s4", "s5", "s6")
PILOT_N = 40
MIN_MAIN_N = 300
D3_N = 100
REPLAY_N = 100
PARAPHRASE_N = 60
SOL_FLOOR_N = 20
PREFLIGHT_PER_KIND = 40
UNSUPPORTED_KINDS = ("num", "offtopic", "extra", "fabricated", "tempt")


class Harness:
    def __init__(self, out: Path, backends: Backends, *, dry_run: bool) -> None:
        self.out = out
        self.backends = backends
        self.dry_run = dry_run
        self.rd = RunData(out)
        self.log = self.rd.log
        self.ledger = Ledger(self.log)
        self.vslice = load_vslice()
        self.v_by_id = {v["vid"]: v for v in self.vslice}
        self.gates_path = out / "gates.json"
        self.gates: dict[str, Any] = json.loads(self.gates_path.read_text(encoding="utf-8")) if self.gates_path.exists() else {}
        self.requeue: list[Callable[[], object]] = []

    # -- plumbing ------------------------------------------------------------------------------
    def save_gate(self, stage: str, passed: bool, **details: Any) -> None:
        self.gates[stage] = {"passed": passed, **details}
        write_json(self.gates_path, self.gates)

    def require(self, stage: str) -> None:
        idx = STAGES.index(stage)
        for prev in STAGES[:idx]:
            if not self.gates.get(prev, {}).get("passed"):
                raise SystemExit(f"{stage} refused: gate {prev} is {'failed' if prev in self.gates else 'missing'}")

    def call(self, key: str, kind: str, meta: dict[str, Any], fn: Callable[[], dict[str, Any]]) -> dict[str, Any]:
        return run_call(self.log, self.ledger, key, kind, meta, fn, requeue=self.requeue)

    def flush_requeue(self) -> None:
        pending, self.requeue = self.requeue, []
        for fn in pending:
            fn()

    def items(self) -> list[dict[str, Any]]:
        n = int(self.gates.get("s2", {}).get("n_main", len(self.rd.items)))
        return self.rd.items[:n]

    # -- the calls -----------------------------------------------------------------------------
    def draft(self, item: dict[str, Any], draw: str) -> dict[str, Any]:
        model = DRAFT_MODEL[draw]
        messages = draft_messages(self.rd.texts(item), item["question"])
        kind = "draft_sol" if draw in ("f1", "f2") else "draft_luna"

        def fn() -> dict[str, Any]:
            first = self.backends.draft(model, messages)
            if first["text"].strip():
                return first
            second = self.backends.draft(model, messages)  # one re-ask on an empty answer (§4.1)
            second["usd"] = float(second.get("usd") or 0.0) + float(first.get("usd") or 0.0)
            second["empty_before_reask"] = True
            if not second["text"].strip():
                second["halt"] = "empty after the re-ask"
            return second

        meta = {"item_id": item["item_id"], "draw": draw, "model": model, "pin": PINS[model],
                "prompt_sha": DRAFT_SYSTEM_SHA, "request": {k: v for k, v in request_spec(model, []).items() if k != "messages"}}
        return self.call(draft_key(item["item_id"], draw), kind, meta, fn)

    def read(self, verifier: str, target: str, excerpts: list[str], question: str, answer: str,
             draw: str = "", prefix: str = "read") -> dict[str, Any]:
        state = decision_state(excerpts, question, answer)
        key = read_key(verifier, target, draw) if prefix == "read" else f"{prefix}|{verifier}|{target}"
        return self.call(key, "jev" if verifier == "jev" else "local", {"target": target, "draw": draw, "verifier": verifier},
                         lambda: self.backends.decide(verifier, state))

    def read_draft(self, verifier: str, item: dict[str, Any], draw: str) -> dict[str, Any] | None:
        text = self.rd.text(item["item_id"], draw)
        if text is None:
            return None
        return self.read(verifier, item["item_id"], self.rd.texts(item), item["question"], text, draw)

    def grade(self, grader: str, item: dict[str, Any], answer: str, target: str, draw: str = "",
              prefix: str = "grade", excerpts: list[str] | None = None) -> dict[str, Any]:
        model = GRADERS[grader]
        messages = [{"role": "system", "content": GRADER_SYSTEM},
                    {"role": "user", "content": grader_user(item, excerpts or self.rd.texts(item), answer)}]

        def fn() -> dict[str, Any]:
            first = self.backends.grade(model, messages)
            if first.get("label"):
                return first
            second = self.backends.grade(model, messages)  # one re-ask on an empty or unreadable label
            second["usd"] = float(second.get("usd") or 0.0) + float(first.get("usd") or 0.0)
            if not second.get("label"):
                second["halt"] = "no readable label after the re-ask"
            return second

        return self.call(grade_key(grader, target, draw, prefix), "grade",
                         {"target": target, "draw": draw, "grader": grader, "model": model, "pin": PINS[model]}, fn)

    def grade_draft(self, item: dict[str, Any], draw: str) -> None:
        text = self.rd.text(item["item_id"], draw)
        if text is None or number_check_fires(text, self.rd.texts(item), item["question"]):
            return  # the number check is first and final: no grader call (Amendment 0, C.6)
        for g in GRADERS:
            self.grade(g, item, text, item["item_id"], draw)

    # -- stages --------------------------------------------------------------------------------
    def s0(self) -> None:
        from chimera.fusion.router import RoutingPolicy

        for v in self.vslice:
            item = self.rd.by_id[v["item_id"]]
            self.read("local", v["vid"], [self.rd.ex[e]["text"] for e in v["excerpt_ids"]], item["question"], v["answer"])
        self.flush_requeue()
        policy = RoutingPolicy()
        census = Counter(policy.fuse_reason(draft_messages(self.rd.texts(i), i["question"])) for i in self.rd.items)  # type: ignore[arg-type]
        violations = []
        for item in self.rd.items:
            for model in (DRAFT_MODEL["d1"], DRAFT_MODEL["f1"], *GRADERS.values()):
                spec = request_spec(model, draft_messages(self.rd.texts(item), item["question"]))
                violations += [f"{item['item_id']}:{model}:{v}" for v in wall_violations(spec)]
        num = [v for v in self.vslice if v["kind"] == "num"]
        gold = [v for v in self.vslice if v["kind"] == "gold"]
        fires = sum(1 for v in num if number_check_fires(v["answer"], [self.rd.ex[e]["text"] for e in v["excerpt_ids"]], v["question"]))
        fires_gold = sum(1 for v in gold if number_check_fires(v["answer"], [self.rd.ex[e]["text"] for e in v["excerpt_ids"]], v["question"]))
        passed = not violations and fires == len(num) and fires_gold == 0
        self.save_gate("s0", passed, fuse_reason=dict(census), wall_violations=violations[:20],
                       number_check={"v_num_fired": fires, "v_num": len(num), "v_gold_fired": fires_gold})

    def preflight_sample(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for kind in ("gold", *UNSUPPORTED_KINDS, "decline"):
            pool = sorted((v for v in self.vslice if v["kind"] == kind), key=lambda v: sha("pre:" + v["vid"]))
            out += pool[:PREFLIGHT_PER_KIND]
        return out

    def s1(self) -> None:
        self.require("s1")
        stopped = self._guard(self._s1_calls)
        self.flush_requeue()
        inst = {ver: instrument_gate(self, ver) for ver in ("jev", "local")}
        graders = {g: grader_gate(self, g) for g in GRADERS}
        flips = replay_flips(self, "jev", "replay")
        both_dropped = all(not r["passed"] for r in inst.values())
        passed = not stopped and not both_dropped and all(r["passed"] for r in graders.values())
        self.save_gate("s1", passed, instrument=inst, graders=graders, jev_replay_flips=flips, admission_stop=stopped,
                       note="both verifiers dropped: stop, publish the V slice" if both_dropped else "")

    def _s1_calls(self) -> None:
        for v in self.vslice:
            item = self.rd.by_id[v["item_id"]]
            self.read("jev", v["vid"], [self.rd.ex[e]["text"] for e in v["excerpt_ids"]], item["question"], v["answer"])
        for v in sorted(self.vslice, key=lambda v: sha("replay:" + v["vid"]))[:REPLAY_N]:
            item = self.rd.by_id[v["item_id"]]
            self.read("jev", v["vid"], [self.rd.ex[e]["text"] for e in v["excerpt_ids"]], item["question"], v["answer"], prefix="replay")
        for v in self.preflight_sample():
            item = self.rd.by_id[v["item_id"]]
            for g in GRADERS:
                self.grade(g, item, v["answer"], v["vid"], excerpts=[self.rd.ex[e]["text"] for e in v["excerpt_ids"]])

    def _guard(self, fn: Callable[[], None]) -> bool:
        """Run a stage's calls; True when the admission stop ended it."""
        try:
            fn()
        except BudgetExhausted as exc:
            print(f"admission stop: {exc}", flush=True)
            return True
        return False

    def s2(self) -> None:
        self.require("s2")
        pilot = self.rd.items[:PILOT_N]

        def calls() -> None:
            for item in pilot:
                for draw in ("d1", "d2", "f1"):
                    self.draft(item, draw)
                    for ver in ("jev", "local"):
                        self.read_draft(ver, item, draw)

        stopped = self._guard(calls)
        self.flush_requeue()
        self.save_gate("s2", False, **pilot_gate(self, pilot, stopped))
        if self.gates["s2"]["mechanics_ok"] and not stopped:
            n_main = size_main(self)
            details = {k: v for k, v in self.gates["s2"].items() if k != "passed"}
            self.save_gate("s2", n_main >= MIN_MAIN_N, **details, n_main=n_main)

    def s3(self) -> None:
        self.require("s3")
        items = self.items()

        def calls() -> None:
            for item in items:
                for draw in ("d1", "d2"):
                    self.draft(item, draw)
                    self.grade_draft(item, draw)
            for item in items[:D3_N]:
                self.draft(item, "d3")
                self.grade_draft(item, "d3")

        stopped = self._guard(calls)
        self.flush_requeue()
        self.write_adjudication_queue(items, ("d1", "d2", "d3"))
        passed, details = base_rate_gate(self, items, stopped)
        self.save_gate("s3", passed, **details)

    def s4(self) -> None:
        self.require("s4")
        items = self.items()

        def calls() -> None:
            for item in items:
                for draw in ("d1", "d2"):
                    for ver in ("jev", "local"):
                        self.read_draft(ver, item, draw)
                if escalated_from_d1(self.rd, item, "jev") or escalated_from_d1(self.rd, item, "local"):
                    self.draft(item, "f1")
                    self.grade_draft(item, "f1")
                    for ver in ("jev", "local"):
                        self.read_draft(ver, item, "f1")

        stopped = self._guard(calls)
        self.flush_requeue()
        self.write_adjudication_queue(items, ("f1",))
        esc = {v: sum(1 for i in items if escalated_from_d1(self.rd, i, v)) for v in ("jev", "local")}
        self.save_gate("s4", not stopped, escalations_from_d1=esc, admission_stop=stopped)

    def s5(self) -> None:
        self.require("s5")
        items = self.items()

        def calls() -> None:
            for v in sorted(self.vslice, key=lambda v: sha("replay:" + v["vid"]))[:REPLAY_N]:
                item = self.rd.by_id[v["item_id"]]
                self.read("local", v["vid"], [self.rd.ex[e]["text"] for e in v["excerpt_ids"]], item["question"], v["answer"], prefix="replay")
            escalated = [i for i in items if escalated_from_d1(self.rd, i, "jev") or escalated_from_d1(self.rd, i, "local")]
            for item in escalated[:SOL_FLOOR_N]:
                self.draft(item, "f2")
                self.grade_draft(item, "f2")
            graded = [i for i in items if self.rd.text(i["item_id"], "d1") is not None]
            for item in graded[:REPLAY_N]:
                text = self.rd.text(item["item_id"], "d1") or ""
                for g in GRADERS:
                    self.grade(g, item, text, item["item_id"], "d1", prefix="regrade")
            for item in graded[REPLAY_N - PARAPHRASE_N : REPLAY_N]:
                text = self.rd.text(item["item_id"], "d1") or ""
                order = permuted(self.rd.texts(item), item["item_id"])
                for g in GRADERS:
                    self.grade(g, item, text, item["item_id"], "d1", prefix="paraphrase", excerpts=order)

        stopped = self._guard(calls)
        self.flush_requeue()
        self.write_adjudication_queue(items, ("f2",))
        self.save_gate("s5", True, local_replay_flips=replay_flips(self, "local", "replay"), admission_stop=stopped)

    def s6(self) -> None:
        self.require("s6")
        items = self.items()
        l_first = sorted(items, key=lambda i: (0 if l_escalates(self, i) else 1, i["order"]))

        def calls() -> None:
            for item in l_first:
                self.draft(item, "f1")
                self.grade_draft(item, "f1")
                for ver in ("jev", "local"):
                    self.read_draft(ver, item, "f1")

        stopped = self._guard(calls)
        self.flush_requeue()
        self.write_adjudication_queue(items, ("f1",))
        covered = sum(1 for i in items if self.rd.text(i["item_id"], "f1") is not None)
        self.save_gate("s6", True, f1_coverage=covered, n=len(items), admission_stop=stopped, spent=round(self.log.spent, 4))

    # -- adjudication --------------------------------------------------------------------------
    def write_adjudication_queue(self, items: list[dict[str, Any]], draws: tuple[str, ...]) -> None:
        queue = []
        for item in items:
            for draw in draws:
                label, source = self.rd.label(item["item_id"], draw)
                if source == "pending" and all(self.rd.votes(item["item_id"], draw).values()):
                    queue.append({"target": f"{item['item_id']}|{draw}", "question": item["question"],
                                  "votes": self.rd.votes(item["item_id"], draw)})
        path = self.out / "adjudication_queue.jsonl"
        existing = {json.loads(ln)["target"] for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()} if path.exists() else set()
        with path.open("a", encoding="utf-8") as fh:
            for row in queue:
                if row["target"] not in existing:
                    fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        if self.dry_run and queue:
            with (self.out / "adjudications.jsonl").open("a", encoding="utf-8") as fh:
                for row in queue:
                    if row["target"] not in self.rd.adjudications:
                        fh.write(json.dumps({"target": row["target"], "label": row["votes"]["g1"], "by": "dry-run: G1's vote"}) + "\n")
                        self.rd.adjudications[row["target"]] = row["votes"]["g1"]


# -- gates ------------------------------------------------------------------------------------------
def instrument_gate(h: Harness, verifier: str) -> dict[str, Any]:
    """S1: dropped if it accepts > 90% of the unsupported constructions, or < 50% of V-gold."""
    acc: dict[str, list[bool]] = {"unsupported": [], "gold": []}
    halts = 0
    for v in h.vslice:
        row = h.log.get(read_key(verifier, v["vid"]))
        if not row:
            continue
        if row.get("status") != "ok":
            halts += 1
            continue
        accepted = row.get("choice") == "supported" and (row.get("p") or 0.0) >= THRESHOLD
        if v["kind"] in UNSUPPORTED_KINDS:
            acc["unsupported"].append(accepted)
        elif v["kind"] == "gold":
            acc["gold"].append(accepted)
    rate = {k: (sum(x) / len(x) if x else None) for k, x in acc.items()}
    passed = rate["unsupported"] is not None and rate["gold"] is not None and rate["unsupported"] <= 0.90 and rate["gold"] >= 0.50
    return {"passed": passed, "accept_unsupported": rate["unsupported"], "accept_gold": rate["gold"],
            "n_unsupported": len(acc["unsupported"]), "n_gold": len(acc["gold"]), "halts": halts}


def v_grading_label(v: dict[str, Any]) -> str:
    if v["label"] == "supported":
        return "correct"
    if v["label"] == "unsupported":
        return "wrong"
    return "declined" if v["family"] == "ANS" else "correct"  # Amendment 0, C.3


def grader_gate(h: Harness, grader: str) -> dict[str, Any]:
    """Gate G (§5.3): agreement >= 90%, recall on the wrong constructions >= 85%, wrong on V-gold <= 5%."""
    agree = recall = gold_wrong = 0
    n = n_wrong = n_gold = 0
    for v in h.preflight_sample():
        row = h.log.get(grade_key(grader, v["vid"]))
        if not row or row.get("status") != "ok":
            continue
        want, got = v_grading_label(v), row.get("label")
        n += 1
        agree += got == want
        if want == "wrong" and v["kind"] != "decline":
            n_wrong += 1
            recall += got == "wrong"
        if v["kind"] == "gold":
            n_gold += 1
            gold_wrong += got == "wrong"
    res = {"n": n, "agreement": agree / n if n else None, "recall_wrong": recall / n_wrong if n_wrong else None,
           "wrong_on_gold": gold_wrong / n_gold if n_gold else None}
    res["passed"] = bool(n) and (res["agreement"] or 0.0) >= 0.90 and (res["recall_wrong"] or 0.0) >= 0.85 and (res["wrong_on_gold"] or 0.0) <= 0.05
    return res


def replay_flips(h: Harness, verifier: str, prefix: str) -> dict[str, int]:
    flips = n = 0
    for v in h.vslice:
        rep = h.log.get(f"{prefix}|{verifier}|{v['vid']}")
        base = h.log.get(read_key(verifier, v["vid"]))
        if rep and base and rep.get("status") == "ok" and base.get("status") == "ok":
            n += 1
            flips += rep.get("choice") != base.get("choice")
    return {"n": n, "flips": flips}


def pilot_gate(h: Harness, pilot: list[dict[str, Any]], stopped: bool) -> dict[str, Any]:
    """S2's gates read mechanics and cost only — never a label (§9)."""
    nonempty: dict[str, list[bool]] = {"luna": [], "sol": []}
    off_pin = halts = mass_ok = mass_n = 0
    readable: dict[str, list[bool]] = {"jev": [], "local": []}
    for item in pilot:
        for draw in ("d1", "d2", "f1"):
            row = h.log.get(draft_key(item["item_id"], draw))
            if row:
                nonempty["sol" if draw == "f1" else "luna"].append(row.get("status") == "ok")
                if row.get("status") == "ok" and str(row.get("provider", "")).lower() != str(row.get("pin", "")).lower():
                    off_pin += 1
            for ver in ("jev", "local"):
                r = h.log.get(read_key(ver, item["item_id"], draw))
                if not r:
                    continue
                ok = r.get("status") == "ok" and r.get("choice") is not None
                readable[ver].append(ok)
                halts += r.get("status") == "halt"
                if ver == "local" and r.get("status") == "ok" and r.get("mass") is not None:
                    mass_n += 1
                    mass_ok += float(r["mass"]) >= 0.5
    rates = {m: (sum(x) / len(x) if x else 0.0) for m, x in nonempty.items()}
    read_rates = {v: (sum(x) / len(x) if x else 0.0) for v, x in readable.items()}
    mass_rate = mass_ok / mass_n if mass_n else 0.0
    unreadable = sum(len(x) - sum(x) for x in readable.values())
    mechanics_ok = all(r >= 0.95 for r in rates.values()) and unreadable <= 2 and mass_rate >= 0.90 and off_pin == 0
    costs = {k: h.log.mean_cost(k) for k in ("draft_luna", "draft_sol", "jev", "grade")}
    return {"nonempty": rates, "readable": read_rates, "halts": halts, "unreadable": unreadable, "local_mass_ok": mass_rate, "off_pin": off_pin,
            "mechanics_ok": mechanics_ok and not stopped, "mean_cost": costs, "admission_stop": stopped}


def size_main(h: Harness) -> int:
    """§10: project S3–S6 over all items at the pilot's mean costs; cut n from the tail to fit."""
    est = {k: h.ledger.estimate(k) for k in ("draft_luna", "draft_sol", "jev", "grade")}
    per_item = (3 * est["draft_luna"] * 1.0 + est["draft_sol"] * 1.05 + 3 * est["jev"] + 4 * est["grade"] * 1.25)
    fixed = 20 * est["draft_sol"] + 320 * est["grade"]  # the Sol floor, grader replay and paraphrase
    room = ADMISSION_STOP_USD - h.log.spent - fixed
    n = len(h.rd.items)
    while n > 0 and n * per_item > room:
        n -= 1
    return n


def base_rate_gate(h: Harness, items: list[dict[str, Any]], stopped: bool) -> tuple[bool, dict[str, Any]]:
    labels = [h.rd.label(i["item_id"], "d1") for i in items]
    pending = sum(1 for lab, src in labels if lab is None and src == "pending")
    graded = sum(1 for lab, _ in labels if lab is not None)
    adjudicated = sum(1 for _, src in labels if src == "adjudicated")
    w = sum(1 for lab, _ in labels if lab == "wrong")
    details: dict[str, Any] = {"W": w, "graded": graded, "pending": pending, "adjudicated": adjudicated, "admission_stop": stopped}
    if pending:
        details["note"] = "awaiting adjudication: fill adjudications.jsonl and re-run s3"
        return False, details
    if graded and adjudicated / graded > 0.15:
        details["note"] = "adjudications exceed 15%: the graders are not doing the job; stop and amend"
        return False, details
    if w < 15:
        details["note"] = "W < 15: stop and publish (§9, S3)"
        return False, details
    if graded and w / graded > 0.40:
        details["note"] = "A's wrong rate exceeds 40%: the prompt or interface is broken; stop and amend"
        return False, details
    return not stopped, details


def l_escalates(h: Harness, item: dict[str, Any]) -> bool:
    out, _ = outcomes(h.rd, item)["L"]
    return out.escalated


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--stage", choices=STAGES)
    ap.add_argument("--dry-run", action="store_true", help="fake backend, no network, every stage unless --stage")
    args = ap.parse_args(argv)
    if not args.dry_run and not args.stage:
        ap.error("a paid run goes one stage at a time: pass --stage")
    backends: Backends
    if args.dry_run:
        backends = FakeBackends()
    else:
        from bench.verified_cascade.backends import LiveBackends

        backends = LiveBackends()
    h = Harness(args.out, backends, dry_run=args.dry_run)
    for stage in [args.stage] if args.stage else list(STAGES):
        try:
            getattr(h, stage)()
        except SystemExit as exc:
            print(exc, flush=True)
            return 2
        gate = h.gates.get(stage, {})
        print(f"{stage}: {'passed' if gate.get('passed') else 'FAILED'}  spent US$ {h.log.spent:.4f}  "
              + json.dumps({k: v for k, v in gate.items() if k not in ('passed',)}, ensure_ascii=False, default=str)[:400], flush=True)
        if not gate.get("passed"):
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
