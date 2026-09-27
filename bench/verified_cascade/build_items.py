"""Fix the excerpt pool and the item skeleton of the verified-cascade bench. US$ 0, stdlib only, offline.

What it fixes, before any question is written and before any model is called (PREREGISTRATION.md §3):

* the **excerpt pool**: the 11 docs that exist in English (``docs/*.md``) AND in Brazilian
  Portuguese (``docs/i18n/pt/*.md``), plus ``README.md`` / ``README.pt-BR.md``, cut at
  ``##``/``###`` headings, each section cut into chunks of about 60-220 words at blank lines,
  never inside a fenced code block;
* the **language split**: an English question may only be written from a section with an even
  index in its document, a Portuguese one only from an odd index, so the two halves never test the
  same passage twice through a translation;
* the **gold chunks**: per language, the eligible chunks in sha256 order, capped at
  ``GOLD_PER_LANG`` and ``MAX_GOLD_PER_DOC`` per document (neither cap binds on this pool);
  eligible = at least 50 words, a heading, and at least one checkable token (a digit, a
  ``--flag``, a ``CHIMERA_`` variable or inline code);
* the **excerpt sets**: for each gold chunk, its 4 nearest chunks of the same language by TF-IDF
  cosine (same document first, the rest of the language's pool after). The answerable item (ANS)
  shows the gold chunk and the 3 nearest; the not-covered item (NCR, "retrieval miss") shows the
  4 nearest and not the gold. The gold chunk's position in ANS is drawn from sha256, and the
  report checks the draw against the uniform (lessons §2u).

Questions, reference answers and key facts are written against this skeleton afterwards; the rules
they must pass are in PREREGISTRATION.md §3.3 and are checked when the item file is frozen.

    python bench/verified_cascade/build_items.py          # writes results/, prints the summary
    python bench/verified_cascade/build_items.py --check  # rebuilds and compares the sha256s
    python bench/verified_cascade/build_items.py freeze   # Amendment 0: items, V slice, manifest
    python bench/verified_cascade/build_items.py freeze --check
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent / "results"
DOCS = (
    "architecture", "benchmarks", "deploy", "extending", "external-agents", "fusion-receipts",
    "index", "mcp", "recipes", "security", "usage", "readme",
)
README = {"en": "README.md", "pt": "README.pt-BR.md"}
MIN_WORDS, MAX_WORDS = 60, 220
GOLD_MIN_WORDS = 50
GOLD_PER_LANG = 130  # a cap the pool does not reach: every eligible chunk is taken (69 en, 75 pt)
MAX_GOLD_PER_DOC = 24
K_NEAR = 4
CHECKABLE = re.compile(r"\d|--[a-z]|CHIMERA_[A-Z_]+|`[^`]+`")
WORD = re.compile(r"[\wÀ-ÿ]+", re.UNICODE)


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def strip_front_matter(text: str) -> tuple[str, str]:
    """The PT files open with ``source_sha256`` — the English version they translate. Kept."""
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    if not m:
        return text, ""
    src = re.search(r"source_sha256:\s*([0-9a-f]+)", m.group(1))
    return text[m.end():], src.group(1) if src else ""


def sections(text: str) -> list[tuple[str, str]]:
    """(heading, body) at ``##``/``###``; the text before the first heading is section 0."""
    out: list[tuple[str, list[str]]] = [("", [])]
    fence = False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            fence = not fence
        if not fence and re.match(r"^#{2,3} ", line):
            out.append((line.strip(), []))
            continue
        out[-1][1].append(line)
    return [(h, "\n".join(b).strip()) for h, b in out if "\n".join(b).strip()]


def blocks(body: str) -> list[str]:
    """Paragraphs at blank lines; a fenced block and an indented admonition body stay whole."""
    out: list[str] = []
    cur: list[str] = []
    fence = False
    for line in body.splitlines():
        if line.lstrip().startswith("```"):
            fence = not fence
        if not fence and not line.strip():
            if cur:
                out.append("\n".join(cur))
                cur = []
            continue
        cur.append(line)
    if cur:
        out.append("\n".join(cur))
    return out


def words(text: str) -> int:
    return len(WORD.findall(text))


def chunk(body: str) -> list[str]:
    """Greedy: add blocks while under MAX_WORDS; a short tail joins the previous chunk."""
    chunks: list[list[str]] = []
    for b in blocks(body):
        short = bool(chunks) and words("\n\n".join(chunks[-1])) < MIN_WORDS
        fits = bool(chunks) and words("\n\n".join(chunks[-1] + [b])) <= MAX_WORDS
        if short or fits:
            chunks[-1].append(b)
        else:
            chunks.append([b])
    if len(chunks) > 1 and words("\n\n".join(chunks[-1])) < MIN_WORDS:
        tail = chunks.pop()
        chunks[-1].extend(tail)
    return ["\n\n".join(c) for c in chunks]


def pool() -> tuple[list[dict[str, Any]], dict[str, str]]:
    rows: list[dict[str, Any]] = []
    sources: dict[str, str] = {}
    for lang, base in (("en", ROOT / "docs"), ("pt", ROOT / "docs" / "i18n" / "pt")):
        for doc in DOCS:
            path = (ROOT / README[lang]) if doc == "readme" else (base / f"{doc}.md")
            raw = path.read_text(encoding="utf-8").replace("\r\n", "\n")
            sources[f"{lang}:{doc}"] = sha(raw)
            text, translated_from = strip_front_matter(raw)
            if translated_from:
                sources[f"{lang}:{doc}:translated_from"] = translated_from
            for s_idx, (heading, body) in enumerate(sections(text)):
                for c_idx, piece in enumerate(chunk(body)):
                    body_text = f"{heading}\n\n{piece}" if heading else piece
                    rows.append({
                        "id": f"{lang}:{doc}:{s_idx}:{c_idx}", "lang": lang, "doc": doc,
                        "section": s_idx, "heading": heading, "words": words(body_text),
                        "text": body_text,
                    })
    return rows, sources


def tfidf(rows: list[dict[str, Any]]) -> list[dict[str, float]]:
    toks = [Counter(w.lower() for w in WORD.findall(r["text"]) if len(w) > 2) for r in rows]
    df = Counter(t for c in toks for t in c)
    n = len(rows)
    vecs = []
    for c in toks:
        v = {t: (1 + math.log(f)) * math.log(n / df[t]) for t, f in c.items()}
        norm = math.sqrt(sum(x * x for x in v.values())) or 1.0
        vecs.append({t: x / norm for t, x in v.items()})
    return vecs


def cos(a: dict[str, float], b: dict[str, float]) -> float:
    if len(a) > len(b):
        a, b = b, a
    return sum(x * b.get(t, 0.0) for t, x in a.items())


def eligible(row: dict[str, Any]) -> bool:
    parity = 0 if row["lang"] == "en" else 1
    return (
        row["section"] % 2 == parity and row["words"] >= GOLD_MIN_WORDS
        and bool(CHECKABLE.search(row["text"])) and bool(row["heading"])
    )


def build() -> dict[str, Any]:
    rows, sources = pool()
    items: list[dict[str, Any]] = []
    for lang in ("en", "pt"):
        lang_rows = [r for r in rows if r["lang"] == lang]
        vecs = tfidf(lang_rows)
        index = {r["id"]: i for i, r in enumerate(lang_rows)}
        cands = sorted((r for r in lang_rows if eligible(r)), key=lambda r: sha(r["id"]))
        per_doc: Counter[str] = Counter()
        gold: list[dict[str, Any]] = []
        for r in cands:
            if per_doc[r["doc"]] >= MAX_GOLD_PER_DOC:
                continue
            per_doc[r["doc"]] += 1
            gold.append(r)
            if len(gold) == GOLD_PER_LANG:
                break
        for rank, g in enumerate(gold):
            gi = index[g["id"]]
            scored = [
                (0 if r["doc"] == g["doc"] else 1, -cos(vecs[gi], vecs[i]), r["id"])
                for i, r in enumerate(lang_rows) if r["id"] != g["id"]
            ]
            near = [rid for _, _, rid in sorted(scored)[:K_NEAR]]
            pos = int(sha("pos:" + g["id"]), 16) % 4
            ans = near[:3]
            ans.insert(pos, g["id"])
            ncr = list(near)
            shift = int(sha("ncr:" + g["id"]), 16) % 4
            ncr = ncr[shift:] + ncr[:shift]
            items.append({
                "gold": g["id"], "lang": lang, "doc": g["doc"], "rank": rank,
                "ans_excerpts": ans, "gold_position": pos, "ncr_excerpts": ncr,
                "spare_neighbours": [rid for _, _, rid in sorted(scored)[K_NEAR:K_NEAR + 4]],
            })
    return {"rows": rows, "sources": sources, "items": items}


def write(result: dict[str, Any]) -> dict[str, str]:
    OUT.mkdir(exist_ok=True)
    files = {
        "excerpts.jsonl": "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in result["rows"]),
        "skeleton.jsonl": "".join(json.dumps(i, ensure_ascii=False) + "\n" for i in result["items"]),
        "sources.json": json.dumps(result["sources"], indent=1, sort_keys=True) + "\n",
    }
    for name, text in files.items():
        (OUT / name).write_bytes(text.encode("utf-8"))
    return {name: sha(text) for name, text in files.items()}


def summary(result: dict[str, Any], digests: dict[str, str]) -> str:
    rows, items = result["rows"], result["items"]
    lines = []
    for lang in ("en", "pt"):
        lr = [r for r in rows if r["lang"] == lang]
        li = [i for i in items if i["lang"] == lang]
        w = sorted(r["words"] for r in lr)
        pos = Counter(i["gold_position"] for i in li)
        exp = len(li) / 4
        chi2 = sum((pos[k] - exp) ** 2 / exp for k in range(4))
        lines.append(
            f"{lang}: {len(lr)} chunks (words median {w[len(w) // 2]}, max {w[-1]}); "
            f"{sum(1 for r in lr if eligible(r))} eligible; {len(li)} gold across "
            f"{len({i['doc'] for i in li})} docs {dict(Counter(i['doc'] for i in li))}; "
            f"gold position {dict(sorted(pos.items()))} chi2(3)={chi2:.2f} (5% critical 7.81)"
        )
    for name, d in digests.items():
        lines.append(f"{name} sha256 {d}")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("action", nargs="?", choices=("build", "freeze"), default="build")
    ap.add_argument("--check", action="store_true", help="rebuild and compare against results/")
    args = ap.parse_args()
    if args.action == "freeze":
        sys.path.insert(0, str(ROOT))
        from bench.verified_cascade.freeze import freeze

        return freeze(check=args.check)
    result = build()
    if args.check:
        want = {n: sha((OUT / n).read_bytes().decode("utf-8")) for n in ("excerpts.jsonl", "skeleton.jsonl", "sources.json")}
        rebuilt = {
            "excerpts.jsonl": sha("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in result["rows"])),
            "skeleton.jsonl": sha("".join(json.dumps(i, ensure_ascii=False) + "\n" for i in result["items"])),
            "sources.json": sha(json.dumps(result["sources"], indent=1, sort_keys=True) + "\n"),
        }
        bad = [n for n in want if want[n] != rebuilt[n]]
        print("identical" if not bad else f"DIFFERENT: {bad}")
        return 1 if bad else 0
    print(summary(result, write(result)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
