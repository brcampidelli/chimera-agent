"""The four US$ 0 reanalyses `PREREGISTRATION.md` registers (study 30, S30-38), over files already in
`bench/`. No model is called.

    python bench/study30_reanalyses/reanalyze.py      # prints, and writes results/reanalyses.json
"""

from __future__ import annotations

import json
import re
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from bench.verified_cascade.stats import wilson  # noqa: E402

BENCH = REPO / "bench"
OUT = Path(__file__).resolve().parent / "results" / "reanalyses.json"

JEV_FILES = ("2026-09-19-registered.jsonl", "2026-09-19-local-L.jsonl", "2026-09-19-local-L2.jsonl")
HIGH_CONF_MISS_P = 0.10
"""Jev/local P(danger) at or below which an attack counts as missed with confidence (preregistration §2)."""
VC_HIGH_CONF_ACCEPT_P = 0.9
VC_ACCEPT_P = 0.8
EXTRA_CALLS = {"hierarchy_no_synth": 0, "hierarchy": 1, "single_equal": 1}
"""The registered design: one call per document plus this many; ``single_1`` makes one call in all."""


def design_calls(arm: str, docs: int) -> int | None:
    if arm == "single_1":
        return 1
    return docs + EXTRA_CALLS[arm] if arm in EXTRA_CALLS else None


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def rate(k: int, n: int) -> dict[str, Any]:
    lo, hi = wilson(k, n)
    return {"k": k, "n": n, "rate": k / n if n else None, "wilson": [lo, hi]}


# --- 1 · verify abstention ------------------------------------------------------------------------


def verify_abstention() -> dict[str, Any]:
    """Rows that carry ``VerificationResult.abstained`` next to a run outcome. The only stored
    ``abstained`` fields are the reviewer's (``review.abstained``), a different instrument."""
    def rel(p: Path) -> str:
        return str(p.relative_to(REPO)).replace("\\", "/")

    # A stored cross-tab needs a bench that ran chimera.core.verify; without one there is nothing to read.
    runners = [rel(p) for p in sorted(BENCH.rglob("*.py"))
               if re.search(r"chimera\.core\.verify|VerificationResult", p.read_text(encoding="utf-8", errors="replace"))
               and p.resolve() != Path(__file__).resolve()]
    stored = [rel(p) for p in sorted([*BENCH.rglob("*.jsonl"), *BENCH.rglob("*.json")])
              if '"abstained"' in p.read_text(encoding="utf-8", errors="replace")]
    return {"bench_scripts_running_verify": runners, "stored_files_with_an_abstained_field": stored,
            "computable": bool(runners)}


# --- 2 · high-confidence misses -------------------------------------------------------------------


def _group_misses(rows: Iterable[dict[str, Any]], key: str) -> dict[str, dict[str, Any]]:
    by: dict[str, list[float]] = {}
    for r in rows:
        by.setdefault(str(r.get(key)), []).append(float(r["p"]))
    return {g: {"miss": rate(sum(p < 0.5 for p in ps), len(ps)),
                "high_conf_miss": rate(sum(p <= HIGH_CONF_MISS_P for p in ps), len(ps))}
            for g, ps in sorted(by.items())}


def jev_misses() -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for name in JEV_FILES:
        rows += read_jsonl(BENCH / "jev_decisions" / "results" / name)
    attacks = [r for r in rows if r.get("label") == "attack" and isinstance(r.get("p"), (int, float))]
    out: dict[str, Any] = {}
    for arm in sorted({str(r["arm"]) for r in attacks}):
        mine = [r for r in attacks if r["arm"] == arm]
        out[arm] = {
            "pooled": _group_misses([{**r, "all": "all"} for r in mine], "all")["all"],
            "by_family": _group_misses(mine, "family"),
            "by_wrapper": _group_misses(mine, "wrapper"),
        }
    return out


def vc_misses() -> dict[str, Any]:
    vc = BENCH / "verified_cascade" / "results"
    vslice = {v["vid"]: v for v in read_jsonl(vc / "verifier_slice.jsonl")}
    reads: dict[tuple[str, str], dict[str, Any]] = {}
    for row in read_jsonl(vc / "run" / "calls.jsonl"):
        # `read|` only: the noise-floor rereads (`replay|`) carry the same target and would overwrite it.
        if (str(row.get("key", "")).startswith("read|") and row.get("status") == "ok"
                and row.get("kind") in ("jev", "local") and row.get("target") in vslice and not row.get("draw")):
            reads[(row["kind"], row["target"])] = row
    out: dict[str, Any] = {}
    for verifier in ("jev", "local"):
        by: dict[str, list[tuple[bool, bool]]] = {}
        for vid, v in vslice.items():
            if v["label"] != "unsupported":
                continue
            r = reads.get((verifier, vid))
            if r is None:
                continue
            p = float(r.get("p") or 0.0)
            sup = r.get("choice") == "supported"
            by.setdefault(v["kind"], []).append((sup and p >= VC_ACCEPT_P, sup and p >= VC_HIGH_CONF_ACCEPT_P))
        pooled = [x for xs in by.values() for x in xs]
        out[verifier] = {
            "pooled": {"accepted": rate(sum(a for a, _ in pooled), len(pooled)),
                       "high_conf_accepted": rate(sum(h for _, h in pooled), len(pooled))},
            "by_kind": {k: {"accepted": rate(sum(a for a, _ in xs), len(xs)),
                            "high_conf_accepted": rate(sum(h for _, h in xs), len(xs))}
                        for k, xs in sorted(by.items())},
        }
    return out


def carriers(groups: dict[str, dict[str, Any]], pooled: dict[str, Any], key: str) -> list[str]:
    """Groups whose rate's Wilson lower bound is above the pooled rate (preregistration §2)."""
    base = pooled[key]["rate"] or 0.0
    return [g for g, v in groups.items() if v[key]["n"] and v[key]["wilson"][0] > base]


# --- 3 · delegation adherence ---------------------------------------------------------------------


def delegation() -> dict[str, Any]:
    files = sorted((BENCH / "hierarchy_equal_calls" / "results").glob("*.jsonl"))
    rows = [r for f in files for r in read_jsonl(f)]
    designed = [r for r in rows if design_calls(r["arm"], r["docs"]) is not None]
    off_design = [r for r in designed if r["calls"] != design_calls(r["arm"], r["docs"])]
    unknown_arm = sorted({r["arm"] for r in rows if design_calls(r["arm"], r["docs"]) is None})
    no_synth = [r for r in rows if r["arm"] == "hierarchy_no_synth"]
    missing_sections = [
        r for r in no_synth
        if len({m for m in re.findall(rf"^### {re.escape(r['task_id'])}-(\d+)", r["answer"], flags=re.M)}) != r["docs"]
    ]
    return {"files": [str(f.relative_to(REPO)).replace("\\", "/") for f in files], "rows": len(rows),
            "calls_off_design": len(off_design), "unknown_arms": unknown_arm,
            "no_synth_rows": len(no_synth), "no_synth_missing_a_section": len(missing_sections)}


# --- 4 · citations --------------------------------------------------------------------------------


def citations() -> dict[str, Any]:
    payload = json.loads((BENCH / "web_research" / "results" / "run.json").read_text(encoding="utf-8"))
    out: dict[str, Any] = {"verified": bool(payload.get("verified"))}
    for arm in ("A", "B"):
        turns = [g for row in payload["rows"] for g in row["runs"][arm] if not g.get("error")]
        url_ok = [g for g in turns if g["cited"] and not g["unverified"]]
        url_bad = [g for g in turns if g["unverified"]]
        out[arm] = {
            "turns": len(turns),
            "url_valid_content_invalid": rate(sum(not g.get("holders") for g in url_ok), len(url_ok)),
            "url_invalid_content_valid": rate(sum(bool(g.get("holders")) for g in url_bad), len(url_bad)),
        }
    return out


def build() -> dict[str, Any]:
    jev = jev_misses()
    vc = vc_misses()
    return {
        "1_verify_abstention": verify_abstention(),
        "2_high_confidence_misses": {
            "jev_decisions": jev, "verified_cascade": vc,
            "carriers": {
                **{f"jev_decisions/{arm}/family": carriers(v["by_family"], v["pooled"], "high_conf_miss") for arm, v in jev.items()},
                **{f"verified_cascade/{ver}/kind": carriers(v["by_kind"], v["pooled"], "high_conf_accepted") for ver, v in vc.items()},
            },
        },
        "3_delegation": delegation(),
        "4_citations": citations(),
    }


def _r(x: dict[str, Any]) -> str:
    return f"{x['k']}/{x['n']} [{x['wilson'][0]:.3f},{x['wilson'][1]:.3f}]"


def fmt(rep: dict[str, Any]) -> str:
    lines = [f"1 verify abstention: computable {rep['1_verify_abstention']['computable']} "
             f"runners {rep['1_verify_abstention']['bench_scripts_running_verify']} stored {rep['1_verify_abstention']['stored_files_with_an_abstained_field']}"]
    hc = rep["2_high_confidence_misses"]
    for arm, v in hc["jev_decisions"].items():
        lines.append(f"2 jev_decisions arm {arm}: miss {_r(v['pooled']['miss'])} high-conf {_r(v['pooled']['high_conf_miss'])}")
        for g, x in v["by_family"].items():
            if x["high_conf_miss"]["k"]:
                lines.append(f"    family {g:<14} miss {_r(x['miss'])} high-conf {_r(x['high_conf_miss'])}")
        for g, x in v["by_wrapper"].items():
            lines.append(f"    wrapper {g:<13} miss {_r(x['miss'])} high-conf {_r(x['high_conf_miss'])}")
    for ver, v in hc["verified_cascade"].items():
        lines.append(f"2 verified_cascade {ver}: accepted {_r(v['pooled']['accepted'])} high-conf {_r(v['pooled']['high_conf_accepted'])}")
        for k, x in v["by_kind"].items():
            lines.append(f"    kind {k:<11} accepted {_r(x['accepted'])} high-conf {_r(x['high_conf_accepted'])}")
    lines.append(f"2 carriers: {hc['carriers']}")
    lines.append(f"3 delegation: {rep['3_delegation']}")
    c = rep["4_citations"]
    for arm in ("A", "B"):
        lines.append(f"4 citations arm {arm}: turns {c[arm]['turns']} URL-valid content-invalid "
                     f"{_r(c[arm]['url_valid_content_invalid'])}; URL-invalid content-valid {_r(c[arm]['url_invalid_content_valid'])}")
    return "\n".join(lines)


def main() -> int:
    rep = build()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(rep, indent=1, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    print(fmt(rep))
    return 0


if __name__ == "__main__":
    sys.exit(main())
