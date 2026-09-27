"""Validate the authored questions and the frozen item files of the verified-cascade bench. Stdlib, offline.

    python bench/verified_cascade/check_items.py                     # the full check on results/
    python bench/verified_cascade/check_items.py --lint-ans FILE     # per-row checks on an authoring file
    python bench/verified_cascade/check_items.py --lint-ncp FILE

The full check asserts what PREREGISTRATION.md §3.3 and Amendment 0 require:

* authored counts per family and language: ANS 69 EN / 75 PT (one per gold slot), NCP 56 / 56;
* frozen counts: ANS and NCR 144 each, NCP 112, minus the drops the freeze report lists;
* every ANS key fact is a (normalized) substring of its gold chunk, and of its reference answer;
* no NCR item's gold chunk is in its excerpts, and no key fact is in any NCR excerpt (§3.3 check 2);
* no NCP tempting term is in its excerpts (check 3);
* the number check does not fire on any reference answer against its ANS excerpts (check 4), and
  fires on every V-num triple and on no V-gold triple (§5.2);
* no duplicate or near-duplicate question (normalized; difflib ratio >= 0.92);
* every item has a source (document and section heading);
* the item files hash to the manifest recorded in ``results/manifest.json``.

Exit status 0 when every check passes, 1 otherwise; every failure is printed.
"""

from __future__ import annotations

import argparse
import difflib
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from bench.verified_cascade.common import (  # noqa: E402
    RESULTS,
    contains,
    norm,
    norm_question,
    number_check_fires,
    read_jsonl,
    sha_file,
)

EXPECTED_ANS = {"en": 69, "pt": 75}
EXPECTED_NCP = {"en": 56, "pt": 56}
NEAR_DUP = 0.92
V_KINDS = ("gold", "num", "offtopic", "extra", "fabricated", "tempt", "decline")


def _excerpts() -> dict[str, dict[str, Any]]:
    return {r["id"]: r for r in read_jsonl(RESULTS / "excerpts.jsonl")}


def _skeleton() -> dict[str, dict[str, Any]]:
    return {s["gold"]: s for s in read_jsonl(RESULTS / "skeleton.jsonl")}


def lint_ans(rows: list[dict[str, Any]]) -> list[str]:
    ex, sk = _excerpts(), _skeleton()
    errs: list[str] = []
    for r in rows:
        g = r.get("gold", "?")
        where = f"ANS {g}"
        if g not in sk:
            errs.append(f"{where}: not a gold slot of the skeleton")
            continue
        slot = sk[g]
        for f in ("question", "reference", "key_facts", "lang", "doc", "heading"):
            if not r.get(f):
                errs.append(f"{where}: missing {f}")
        if r.get("lang") != slot["lang"] or r.get("doc") != slot["doc"]:
            errs.append(f"{where}: lang/doc differ from the skeleton")
        if r.get("heading") and r["heading"] != ex[g]["heading"]:
            errs.append(f"{where}: heading is not the gold chunk's heading")
        facts = r.get("key_facts") or []
        if not 1 <= len(facts) <= 3:
            errs.append(f"{where}: {len(facts)} key facts (1 to 3)")
        for kf in facts:
            if not contains(ex[g]["text"], kf):
                errs.append(f"{where}: key fact {kf!r} not in the gold chunk")
            if not contains(r.get("reference", ""), kf):
                errs.append(f"{where}: key fact {kf!r} not in the reference answer")
            if kf in r.get("question", ""):
                errs.append(f"{where}: key fact {kf!r} is given away in the question")
        bad = [e for e in slot["ncr_excerpts"] if any(contains(ex[e]["text"], kf) for kf in facts)]
        free = [s for s in slot["spare_neighbours"] if not any(contains(ex[s]["text"], kf) for kf in facts)]
        if bad:
            level = "warning-level, replaceable: " if len(free) >= len(bad) else ""
            errs.append(
                f"{where}: {level}a key fact is in NCR excerpt(s) {bad}"
                f" ({len(free)} of 4 spares free of every key fact)"
            )
        excerpts = [ex[e]["text"] for e in slot["ans_excerpts"]]
        if number_check_fires(r.get("reference", ""), excerpts, r.get("question", "")):
            errs.append(f"{where}: the number check fires on the reference answer")
    return errs


def lint_ncp(rows: list[dict[str, Any]]) -> list[str]:
    from bench.verified_cascade.common import TfIdf, nearest_for_question

    ex = _excerpts()
    by_lang = {lang: [r for r in ex.values() if r["lang"] == lang] for lang in ("en", "pt")}
    models = {lang: TfIdf([r["text"] for r in rs]) for lang, rs in by_lang.items()}
    errs: list[str] = []
    for r in rows:
        where = f"NCP {r.get('pid', '?')}"
        for f in ("pid", "question", "tempting_answer", "tempting_terms", "why_wrong", "lang", "doc", "heading"):
            if not r.get(f):
                errs.append(f"{where}: missing {f}")
        lang, doc = r.get("lang"), r.get("doc")
        if lang not in models:
            continue
        headings = {x["heading"] for x in by_lang[lang] if x["doc"] == doc}
        if r.get("heading") not in headings:
            errs.append(f"{where}: heading {r.get('heading')!r} is not a heading of {lang}:{doc}")
        near = nearest_for_question(r.get("question", ""), doc or "", by_lang[lang], models[lang])
        doc_text = "\n".join(x["text"] for x in by_lang[lang] if x["doc"] == doc)
        for term in r.get("tempting_terms") or []:
            if not contains(r.get("tempting_answer", ""), term):
                errs.append(f"{where}: tempting term {term!r} not in the tempting answer")
            hits = [e for e in near if contains(ex[e]["text"], term)]
            if hits:
                errs.append(f"{where}: tempting term {term!r} is in its excerpts {hits}")
            elif contains(doc_text, term):
                errs.append(f"{where}: tempting term {term!r} appears elsewhere in {lang}:{doc} (warning-level: pick another)")
    return errs


def duplicates(questions: list[tuple[str, str]]) -> list[str]:
    errs: list[str] = []
    seen: dict[str, str] = {}
    normed = [(qid, norm_question(q)) for qid, q in questions]
    for qid, n in normed:
        if n in seen:
            errs.append(f"duplicate question: {qid} = {seen[n]}")
        seen[n] = qid
    for i in range(len(normed)):
        for j in range(i + 1, len(normed)):
            a, b = normed[i][1], normed[j][1]
            if abs(len(a) - len(b)) > 0.2 * max(len(a), len(b), 1):
                continue
            sm = difflib.SequenceMatcher(None, a, b)
            if sm.real_quick_ratio() >= NEAR_DUP and sm.quick_ratio() >= NEAR_DUP and sm.ratio() >= NEAR_DUP:
                errs.append(f"near-duplicate questions: {normed[i][0]} ~ {normed[j][0]}")
    return errs


def full() -> list[str]:
    errs: list[str] = []
    ans = read_jsonl(RESULTS / "questions_ans.jsonl")
    ncp = read_jsonl(RESULTS / "questions_ncp.jsonl")
    items = read_jsonl(RESULTS / "items.jsonl")
    vslice = read_jsonl(RESULTS / "verifier_slice.jsonl")
    if not (ans and ncp and items and vslice):
        return ["missing results files: run build_items.py freeze first"]
    ex, sk = _excerpts(), _skeleton()

    got = Counter(r["lang"] for r in ans)
    if dict(got) != EXPECTED_ANS:
        errs.append(f"authored ANS per language {dict(got)} != {EXPECTED_ANS}")
    if {r["gold"] for r in ans} != set(sk):
        errs.append("authored ANS questions do not cover the skeleton's gold slots one to one")
    got = Counter(r["lang"] for r in ncp)
    if dict(got) != EXPECTED_NCP:
        errs.append(f"authored NCP per language {dict(got)} != {EXPECTED_NCP}")
    errs += [e for e in lint_ans(ans) if "warning-level" not in e]
    errs += [e for e in lint_ncp(ncp) if "warning-level" not in e]
    errs += duplicates([(r["gold"], r["question"]) for r in ans] + [(r["pid"], r["question"]) for r in ncp])

    manifest = json.loads((RESULTS / "manifest.json").read_text(encoding="utf-8"))
    dropped = manifest.get("dropped", {})
    fam = Counter((i["family"], i["lang"]) for i in items)
    for (family, expected) in (("ANS", EXPECTED_ANS), ("NCR", EXPECTED_ANS), ("NCP", EXPECTED_NCP)):
        for lang, n in expected.items():
            want = n - int(dropped.get(f"{family}:{lang}", 0))
            if fam[(family, lang)] != want:
                errs.append(f"frozen {family} {lang}: {fam[(family, lang)]} items, expected {want}")
    ids = [i["item_id"] for i in items]
    if len(ids) != len(set(ids)):
        errs.append("duplicate item ids")
    for i in items:
        where = i["item_id"]
        if not (i.get("source") or {}).get("doc") or not (i.get("source") or {}).get("heading"):
            errs.append(f"{where}: no source")
        texts = [ex[e]["text"] for e in i["excerpt_ids"]]
        if len(texts) != 4:
            errs.append(f"{where}: {len(texts)} excerpts")
        if i["family"] == "ANS" and i["gold"] not in i["excerpt_ids"]:
            errs.append(f"{where}: gold chunk not in the excerpts")
        if i["family"] == "NCR":
            if i["gold"] in i["excerpt_ids"]:
                errs.append(f"{where}: gold chunk present in an NCR context")
            for kf in i["key_facts"]:
                if any(contains(t, kf) for t in texts):
                    errs.append(f"{where}: key fact {kf!r} in an NCR excerpt")
        if i["family"] == "NCP":
            for term in i["tempting_terms"]:
                if any(contains(t, term) for t in texts):
                    errs.append(f"{where}: tempting term {term!r} in its excerpts")
        if i["family"] == "ANS" and number_check_fires(i["reference"], texts, i["question"]):
            errs.append(f"{where}: number check fires on the reference")

    by_item = {i["item_id"]: i for i in items}
    kinds = Counter(v["kind"] for v in vslice)
    for k in V_KINDS:
        if not kinds[k]:
            errs.append(f"verifier slice has no {k} triples")
    for v in vslice:
        it = by_item.get(v["item_id"])
        if it is None:
            errs.append(f"V {v['vid']}: unknown item")
            continue
        texts = [ex[e]["text"] for e in v["excerpt_ids"]]
        fires = number_check_fires(v["answer"], texts, v["question"])
        if v["kind"] == "num" and not fires:
            errs.append(f"V {v['vid']}: the number check does not fire on a V-num triple")
        if v["kind"] == "gold" and fires:
            errs.append(f"V {v['vid']}: the number check fires on a V-gold triple")
        want_label = {"gold": "supported", "decline": "declined"}.get(v["kind"], "unsupported")
        if v["label"] != want_label:
            errs.append(f"V {v['vid']}: label {v['label']} for kind {v['kind']}")
        if v["kind"] == "fabricated" and it["gold"] in v["excerpt_ids"]:
            errs.append(f"V {v['vid']}: V-fabricated shows the gold chunk")

    for name, digest in manifest.get("files", {}).items():
        path = RESULTS / name
        if not path.exists() or sha_file(path) != digest:
            errs.append(f"manifest: {name} does not hash to {digest[:12]}")
    return errs


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--lint-ans", type=Path)
    ap.add_argument("--lint-ncp", type=Path)
    args = ap.parse_args()
    if args.lint_ans or args.lint_ncp:
        errs: list[str] = []
        if args.lint_ans:
            rows = read_jsonl(args.lint_ans)
            errs += lint_ans(rows) + duplicates([(r.get("gold", "?"), r.get("question", "")) for r in rows])
            print(f"{len(rows)} ANS rows")
        if args.lint_ncp:
            rows = read_jsonl(args.lint_ncp)
            errs += lint_ncp(rows) + duplicates([(r.get("pid", "?"), r.get("question", "")) for r in rows])
            print(f"{len(rows)} NCP rows")
    else:
        errs = full()
    for e in errs:
        print("FAIL", e)
    print("OK" if not errs else f"{len(errs)} failure(s)")
    return 0 if not errs else 1


if __name__ == "__main__":
    sys.exit(main())


__all__ = ["full", "lint_ans", "lint_ncp", "duplicates", "norm"]
