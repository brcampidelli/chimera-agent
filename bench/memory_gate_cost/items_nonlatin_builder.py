"""Build ``items_nonlatin.jsonl`` — the non-Latin strata of the memory-gate cost bench.

    python bench/memory_gate_cost/items_nonlatin_builder.py

See ``PREREGISTRATION-nonlatin.md`` (written before ``run_nonlatin.py`` was executed). Deterministic:
the same tree and the same seeds give the same file, byte for byte.

Why this exists: ``items.jsonl`` is English documentation read with an English query, which is the
one condition under which the gate's ASCII tokenizer cannot fail. A bench that cannot exhibit the
defect cannot refute it, and 0 of 120 lost was read as "the gate costs nothing" (audit finding
a05-08). The facts here come from the repository's own translations of its documentation, so the
population is the same text in the scripts the app ships in.

The query is NOT built to dodge the defect or to trigger it: it is the fact's own longest content
words, whatever script they are in. If a sentence carries an ASCII word the old gate can see, that
word may be in the query, and the old gate is credited with it; the runner reports how many rows
that applies to rather than assuming it is none.
"""

from __future__ import annotations

import json
import random
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from chimera.memory.tokens import informative, tokens  # noqa: E402

OUT = Path(__file__).resolve().parent / "items_nonlatin.jsonl"

#: Three corpus draws. Each is a fresh sample WITHOUT replacement from the same pool, so they are
#: three samples of one population, not three populations; the registration says so.
SEEDS = (20261114, 20261115, 20261116)
N_PER_STRATUM = 30
LANGS = ("ru", "zh", "ja", "pt")

_SENTENCE_END = {
    "ru": r"(?<=[.!?])\s+",
    "pt": r"(?<=[.!?])\s+",
    "zh": r"(?<=[。！？])",
    "ja": r"(?<=[。！？])",
}
_CYRILLIC = re.compile(r"[Ѐ-ӿ]")
_HAN = re.compile(r"[㐀-䶿一-鿿]")
_KANA = re.compile(r"[぀-ヿ]")
_LETTER = re.compile(r"[^\W\d_]")
_ACCENT = re.compile(r"[ãõçáéíóúâêô]", re.I)

#: Function characters the per-character Han tokenizer cannot tell from content ones. Left out of
#: the QUERY only (a query of "的的了" says nothing); the fact is untouched.
_HAN_FUNCTION = frozenset("的了在是和与及或不也中为对有将")


def _prose_lines(text: str) -> list[str]:
    out: list[str] = []
    fenced = False
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("```"):
            fenced = not fenced
            continue
        if fenced or not line or line.startswith(("#", "|", "<", ">", "![", "[!")):
            continue
        out.append(re.sub(r"^([-*+]|\d+\.)\s+", "", line))
    return out


def _clean(text: str) -> str:
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    return text.replace("**", "").replace("`", "")


def _in_script(lang: str, sentence: str) -> bool:
    letters = _LETTER.findall(sentence)
    if not letters:
        return False
    share = lambda rx: sum(1 for c in letters if rx.match(c)) / len(letters)  # noqa: E731
    if lang == "ru":
        return share(_CYRILLIC) >= 0.5
    if lang == "zh":
        return share(_HAN) >= 0.5
    if lang == "ja":
        return share(_HAN) + share(_KANA) >= 0.5 and bool(_KANA.search(sentence))
    # pt: Latin script by nature; require an accented letter so the accent defect is exercised.
    return bool(_ACCENT.search(sentence))


def _size_ok(lang: str, sentence: str) -> bool:
    if not sentence.endswith((".", "!", "?", "。", "！", "？")):
        return False
    if lang in ("zh", "ja"):
        return 15 <= len(sentence) <= 120
    return 6 <= len(re.findall(r"\w+", sentence)) <= 50


def _pool(lang: str) -> list[tuple[str, str]]:
    seen: set[str] = set()
    pool: list[tuple[str, str]] = []
    for path in sorted((ROOT / "docs" / "i18n" / lang).glob("*.md")):
        joined = _clean(" ".join(_prose_lines(path.read_text(encoding="utf-8"))))
        for part in re.split(_SENTENCE_END[lang], joined):
            sentence = part.strip()
            if _size_ok(lang, sentence) and _in_script(lang, sentence) and sentence not in seen:
                seen.add(sentence)
                pool.append((path.name, sentence))
    return pool


def query_for(lang: str, content: str) -> str:
    """The fact's own content words, in its own script, no frame sentence."""
    toks = [t for t in tokens(content) if t not in _HAN_FUNCTION]
    terms = informative(toks)
    if lang == "zh":
        # Per-character tokens are all one length: take four distinct characters spread along the
        # sentence, so the query is about the sentence and not about its first clause.
        distinct: list[str] = []
        for t in toks:
            if t in terms and t not in distinct:
                distinct.append(t)
        step = max(1, len(distinct) // 4)
        return "".join(distinct[::step][:4])
    ranked = sorted(terms, key=lambda w: (-len(w), w))[:3]
    return " ".join(sorted(ranked))


def build() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for seed in SEEDS:
        for lang in LANGS:
            pool = _pool(lang)
            if len(pool) < N_PER_STRATUM:
                raise SystemExit(f"{lang}: pool of {len(pool)} is smaller than {N_PER_STRATUM}")
            rng = random.Random(f"{seed}:{lang}")
            for i, (source, content) in enumerate(rng.sample(pool, N_PER_STRATUM)):
                rows.append({
                    "id": f"{lang}_{seed}_{i:02d}", "arm": "honest", "stratum": lang, "seed": seed,
                    "source": f"docs/i18n/{lang}/{source}", "content": content,
                    "query": query_for(lang, content),
                })
    return rows


def main() -> None:
    rows = build()
    with OUT.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    pools = {lang: len(_pool(lang)) for lang in LANGS}
    print(f"wrote {len(rows)} rows to {OUT.name}; pool sizes {pools}")


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from _legacy_defaults import pin_legacy_defaults

    pin_legacy_defaults()  # no model runs here; pinned anyway so every bench entry point reads the same
    main()
