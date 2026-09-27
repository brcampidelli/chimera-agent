"""The freeze of Amendment 0: authored questions -> ``items.jsonl`` + ``verifier_slice.jsonl`` + manifest.

Called as ``python bench/verified_cascade/build_items.py freeze`` (``--check`` rebuilds and compares).
Deterministic, stdlib, offline. Reads the committed skeleton and the authored files, applies the
mechanical checks of PREREGISTRATION.md §3.3 (an item that fails is dropped and counted, never
edited), builds the V slice of §3.4, and writes a manifest of sha256s that Amendment 0 records.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from bench.verified_cascade.common import (
    RESULTS,
    TfIdf,
    contains,
    digit_runs,
    jsonl_text,
    nearest_for_question,
    number_check_fires,
    read_jsonl,
    sha,
)

DECLINES = {
    "en": (
        "The provided excerpts do not cover this question.",
        "I can't answer that from the excerpts: they don't contain this information.",
        "The excerpts don't say, so I can't give an answer based on them.",
    ),
    "pt": (
        "Os trechos fornecidos não cobrem esta pergunta.",
        "Não consigo responder com base nos trechos: eles não trazem essa informação.",
        "Os trechos não dizem isso, então não posso dar uma resposta a partir deles.",
    ),
}
AUTHORED = ("questions_ans.jsonl", "questions_ncp.jsonl")
FROZEN = ("items.jsonl", "verifier_slice.jsonl", "freeze_report.txt")
POOL = ("excerpts.jsonl", "skeleton.jsonl", "sources.json")


def _pick(key: str, n: int) -> int:
    return int(sha(key), 16) % n


def _ncr_excerpts(slot: dict[str, Any], facts: list[str], ex: dict[str, dict[str, Any]]) -> tuple[list[str] | None, list[str]]:
    """Check 2: an NCR excerpt holding a key fact is replaced by the next clean spare, in order."""
    spares = [s for s in slot["spare_neighbours"] if not any(contains(ex[s]["text"], f) for f in facts)]
    out: list[str] = []
    replaced: list[str] = []
    for e in slot["ncr_excerpts"]:
        if any(contains(ex[e]["text"], f) for f in facts):
            if not spares:
                return None, replaced
            new = spares.pop(0)
            replaced.append(f"{e}->{new}")
            out.append(new)
        else:
            out.append(e)
    return out, replaced


def _mutate_number(item: dict[str, Any], texts: list[str]) -> str | None:
    """V-num: one digit of a numeric key fact moved d -> d+1 mod 10, the new run absent everywhere."""
    allowed = digit_runs(item["question"])
    for t in texts:
        allowed |= digit_runs(t)
    ref = item["reference"]
    for kf in item["key_facts"]:
        runs = [r for r in re.findall(r"\d+", kf) if re.search(rf"(?<!\d){r}(?!\d)", ref)]
        for run in runs:
            start = _pick("num:" + item["item_id"] + ":" + run, len(run))
            for k in range(len(run)):
                pos = (start + k) % len(run)
                new = run[:pos] + str((int(run[pos]) + 1) % 10) + run[pos + 1 :]
                if new in allowed:
                    continue
                answer = re.sub(rf"(?<!\d){run}(?!\d)", new, ref)
                if number_check_fires(answer, texts, item["question"]):
                    return answer
    return None


def _first_sentence(text: str) -> str:
    m = re.match(r"(.+?[.!?])(\s|$)", text.strip(), re.S)
    return (m.group(1) if m else text).strip()


def build_items(ex: dict[str, dict[str, Any]]) -> tuple[list[dict[str, Any]], Counter[str], list[str]]:
    skeleton = {s["gold"]: s for s in read_jsonl(RESULTS / "skeleton.jsonl")}
    ans_q = read_jsonl(RESULTS / "questions_ans.jsonl")
    ncp_q = read_jsonl(RESULTS / "questions_ncp.jsonl")
    items: list[dict[str, Any]] = []
    dropped: Counter[str] = Counter()
    notes: list[str] = []
    for q in ans_q:
        slot = skeleton[q["gold"]]
        lang, facts = slot["lang"], list(q["key_facts"])
        base = {
            "question_id": f"q:{q['gold']}", "lang": lang, "doc": slot["doc"], "question": q["question"],
            "reference": q["reference"], "key_facts": facts, "gold": q["gold"],
            "source": {"doc": slot["doc"], "heading": ex[q["gold"]]["heading"], "chunk": q["gold"]},
        }
        facts_ok = all(contains(ex[q["gold"]]["text"], f) for f in facts)
        ans_texts = [ex[e]["text"] for e in slot["ans_excerpts"]]
        if not facts_ok or number_check_fires(q["reference"], ans_texts, q["question"]):
            dropped[f"ANS:{lang}"] += 1
            notes.append(f"dropped ANS:{q['gold']} (check 1 or 4)")
        else:
            items.append({"item_id": f"ANS:{q['gold']}", "family": "ANS", **base,
                          "excerpt_ids": list(slot["ans_excerpts"]), "gold_position": slot["gold_position"]})
        ncr, replaced = _ncr_excerpts(slot, facts, ex)
        if not facts_ok or ncr is None:
            dropped[f"NCR:{lang}"] += 1
            notes.append(f"dropped NCR:{q['gold']} (check 1 or 2)")
        else:
            if replaced:
                notes.append(f"NCR:{q['gold']} replaced {replaced}")
            items.append({"item_id": f"NCR:{q['gold']}", "family": "NCR", **base, "excerpt_ids": ncr,
                          "ncr_replaced": replaced})
    by_lang = {lang: [r for r in ex.values() if r["lang"] == lang] for lang in ("en", "pt")}
    models = {lang: TfIdf([r["text"] for r in rows]) for lang, rows in by_lang.items()}
    for q in ncp_q:
        lang = q["lang"]
        near = nearest_for_question(q["question"], q["doc"], by_lang[lang], models[lang])
        if any(contains(ex[e]["text"], t) for e in near for t in q["tempting_terms"]):
            dropped[f"NCP:{lang}"] += 1
            notes.append(f"dropped {q['pid']} (check 3)")
            continue
        items.append({
            "item_id": f"NCP:{q['pid']}", "family": "NCP", "question_id": q["pid"], "lang": lang,
            "doc": q["doc"], "question": q["question"], "reference": "", "key_facts": [], "gold": None,
            "tempting_answer": q["tempting_answer"], "tempting_terms": list(q["tempting_terms"]),
            "why_wrong": q["why_wrong"], "excerpt_ids": near,
            "source": {"doc": q["doc"], "heading": q["heading"], "chunk": None},
        })
    items.sort(key=lambda i: sha(i["item_id"]))
    for rank, i in enumerate(items):
        i["order"] = rank
    return items, dropped, notes


def build_vslice(items: list[dict[str, Any]], ex: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    by_gold = {i["gold"]: i for i in items if i["family"] == "ANS"}
    out: list[dict[str, Any]] = []

    def add(item: dict[str, Any], kind: str, answer: str, label: str, excerpts: list[str] | None = None) -> None:
        out.append({
            "vid": f"V-{kind}:{item['item_id']}", "kind": kind, "item_id": item["item_id"],
            "family": item["family"], "lang": item["lang"], "doc": item["doc"], "question_id": item["question_id"],
            "question": item["question"], "excerpt_ids": list(excerpts or item["excerpt_ids"]),
            "answer": answer, "label": label,
        })

    for item in items:
        texts = [ex[e]["text"] for e in item["excerpt_ids"]]
        fam, lang = item["family"], item["lang"]
        if fam == "ANS":
            add(item, "gold", item["reference"], "supported")
            mutated = _mutate_number(item, texts)
            if mutated is not None:
                add(item, "num", mutated, "unsupported")
            others = sorted(
                (i for i in items if i["family"] == "ANS" and i["lang"] == lang and i["doc"] != item["doc"]),
                key=lambda i: i["item_id"],
            )
            if others:
                donor = others[_pick("extra:" + item["item_id"], len(others))]
                add(item, "extra", item["reference"].rstrip() + " " + _first_sentence(donor["reference"]), "unsupported")
        if fam in ("ANS", "NCR"):
            candidates = sorted(g for g in item["excerpt_ids"] if g != item["gold"] and g in by_gold)
            if candidates:
                donor = by_gold[candidates[_pick("off:" + item["item_id"], len(candidates))]]
                add(item, "offtopic", donor["reference"], "unsupported")
        if fam == "NCR":
            add(item, "fabricated", item["reference"], "unsupported")
        if fam == "NCP":
            add(item, "tempt", item["tempting_answer"], "unsupported")
        decl = DECLINES[lang]
        add(item, "decline", decl[_pick("decl:" + item["item_id"], len(decl))], "declined")
    out.sort(key=lambda v: sha(v["vid"]))
    return out


def report(items: list[dict[str, Any]], vslice: list[dict[str, Any]], dropped: Counter[str],
           notes: list[str], ex: dict[str, dict[str, Any]]) -> str:
    lines = ["freeze report (Amendment 0)", ""]
    fam = Counter((i["family"], i["lang"]) for i in items)
    for f in ("ANS", "NCR", "NCP"):
        lines.append(f"{f}: en {fam[(f, 'en')]}, pt {fam[(f, 'pt')]}, dropped {dict((k, v) for k, v in dropped.items() if k.startswith(f))}")
    lines.append(f"items {len(items)}; distinct questions {len({i['question_id'] for i in items})}")
    for f in ("ANS", "NCR", "NCP"):
        sel = [i for i in items if i["family"] == f]
        if not sel:
            continue
        ew = sorted(sum(ex[e]["words"] for e in i["excerpt_ids"]) for i in sel)
        qw = sorted(len(i["question"].split()) for i in sel)
        lines.append(f"{f} excerpt words median {ew[len(ew) // 2]} (min {ew[0]}, max {ew[-1]}); question words median {qw[len(qw) // 2]}")
    vk = Counter((v["kind"], v["lang"]) for v in vslice)
    lines.append(f"V slice {len(vslice)} triples: " + ", ".join(
        f"{k} {vk[(k, 'en')]}+{vk[(k, 'pt')]}" for k in ("gold", "num", "offtopic", "extra", "fabricated", "tempt", "decline")))
    fires_num = sum(1 for v in vslice if v["kind"] == "num" and number_check_fires(v["answer"], [ex[e]["text"] for e in v["excerpt_ids"]], v["question"]))
    fires_gold = sum(1 for v in vslice if v["kind"] == "gold" and number_check_fires(v["answer"], [ex[e]["text"] for e in v["excerpt_ids"]], v["question"]))
    lines.append(f"number check self-test: fires on {fires_num}/{vk[('num', 'en')] + vk[('num', 'pt')]} V-num, {fires_gold} V-gold")
    lines.append("")
    lines += notes
    return "\n".join(lines) + "\n"


def freeze(check: bool = False) -> int:
    ex = {r["id"]: r for r in read_jsonl(RESULTS / "excerpts.jsonl")}
    items, dropped, notes = build_items(ex)
    vslice = build_vslice(items, ex)
    texts = {
        "items.jsonl": jsonl_text(items),
        "verifier_slice.jsonl": jsonl_text(vslice),
        "freeze_report.txt": report(items, vslice, dropped, notes, ex),
    }
    files = {n: sha((RESULTS / n).read_bytes().decode("utf-8")) for n in POOL + AUTHORED}
    files.update({n: sha(t) for n, t in texts.items()})
    manifest = {
        "amendment": 0, "files": dict(sorted(files.items())),
        "dropped": dict(sorted(dropped.items())),
        "counts": dict(sorted(Counter(f"{i['family']}:{i['lang']}" for i in items).items())),
        "v_counts": dict(sorted(Counter(v["kind"] for v in vslice).items())),
    }
    texts["manifest.json"] = json.dumps(manifest, indent=1, sort_keys=True) + "\n"
    if check:
        bad = [n for n, t in texts.items() if not (RESULTS / n).exists() or (RESULTS / n).read_bytes().decode("utf-8") != t]
        print("identical" if not bad else f"DIFFERENT: {bad}")
        return 1 if bad else 0
    for n, t in texts.items():
        Path(RESULTS / n).write_bytes(t.encode("utf-8"))
    print(texts["freeze_report.txt"].split("\n\n")[0])
    print(f"manifest.json sha256 {sha(texts['manifest.json'])}")
    return 0
