"""Build the frozen items of `bench/grounded_task_declines` (PREREGISTRATION.md §2). Deterministic, US$ 0.

- **Tasks (T):** 60 = 2 languages x 6 kinds x 5 phrasings. Each phrasing is used once, on its own
  excerpt drawn from `bench/verified_cascade/results/excerpts.jsonl` (the same pool the gate was
  measured on), in the task's language, 80-300 words, no excerpt used twice.
- **Control (C):** 30 answerable questions (ANS), 15 per language, drawn from
  `bench/verified_cascade/results/items.jsonl` with the excerpts that item was measured with.

``python -m bench.grounded_task_declines.build_items`` writes ``results/items.jsonl`` and prints its
sha256; ``--check`` rebuilds in memory and exits 1 if the file on disk differs.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
from pathlib import Path

HERE = Path(__file__).resolve().parent
POOL = HERE.parent / "verified_cascade" / "results"
OUT = HERE / "results" / "items.jsonl"
SEED = 20260927

#: Written before any model call, by the reviewing session. About a third of each kind is shaped
#: like the five tasks the classifier misread on the fresh set (polite frame, advice, appraisal).
PHRASINGS: dict[str, dict[str, list[str]]] = {
    "pt": {
        "summarize": [
            "Resuma este trecho em três tópicos.",
            "Será que dá pra fazer um resumo curto disso?",
            "me dá um resumão disso aqui",
            "Faça um resumo executivo do documento anexado.",
            "Você poderia resumir isso em uma frase?",
        ],
        "critique": [
            "Critique a clareza deste texto e aponte o que está confuso.",
            "O que você acha da forma como isso está explicado?",
            "Quais são os pontos fracos dessa documentação?",
            "Revise este trecho e diga onde falta informação para um iniciante.",
            "Esse texto está bem escrito? Justifique.",
        ],
        "translate": [
            "Traduza este trecho para o inglês.",
            "Será que dá pra passar isso pro inglês?",
            "traduz pro espanhol por favor",
            "Você poderia traduzir o documento para o inglês mantendo os termos técnicos?",
            "Me entrega uma versão em inglês deste texto.",
        ],
        "rewrite": [
            "Reescreva este trecho em linguagem simples.",
            "Dá pra deixar isso mais curto?",
            "Transforme este texto em uma lista de passos.",
            "Você poderia reescrever isso num tom mais informal?",
            "Deixa esse texto mais claro pra quem nunca usou o programa.",
        ],
        "appraise": [
            "Essa abordagem parece segura pra mim?",
            "Isso é uma boa ideia para uma equipe pequena?",
            "A forma descrita aqui é adequada para uso em produção? Justifique.",
            "Vale a pena adotar o que está descrito aqui?",
            "O que você acha dessa decisão de design?",
        ],
        "advise": [
            "O que você me recomenda fazer primeiro, com base nisso?",
            "Me ajuda a decidir se devo usar esse recurso?",
            "Monte um plano de três passos pra eu começar a usar isso.",
            "O que você mudaria nessa configuração?",
            "Me dê sua opinião: devo ligar isso já ou esperar?",
        ],
    },
    "en": {
        "summarize": [
            "Summarize this section in three bullets.",
            "Could you give me a short summary of this?",
            "tl;dr this pls",
            "Write an executive summary of the attached document.",
            "Can you boil this down to one sentence?",
        ],
        "critique": [
            "Critique the clarity of this text and point out what is confusing.",
            "What do you think of how this is explained?",
            "What are the weak spots in this documentation?",
            "Review this passage and say where a beginner would get lost.",
            "Is this well written? Explain why or why not.",
        ],
        "translate": [
            "Translate this passage into Portuguese.",
            "Could you put this into Spanish?",
            "translate to french please",
            "Can you translate the document into Brazilian Portuguese, keeping the technical terms?",
            "Give me a German version of this text.",
        ],
        "rewrite": [
            "Rewrite this passage in plain language.",
            "Can you make this shorter?",
            "Turn this text into a list of steps.",
            "Could you rewrite this in a more casual tone?",
            "Make this clearer for someone who has never used the tool.",
        ],
        "appraise": [
            "Does this approach seem safe to me?",
            "Is this a good idea for a small team?",
            "Is the setup described here fit for production use? Justify.",
            "Is it worth adopting what is described here?",
            "What do you think of this design decision?",
        ],
        "advise": [
            "What would you recommend I do first, based on this?",
            "Help me decide whether I should use this feature.",
            "Put together a three-step plan for me to start using this.",
            "What would you change in this setup?",
            "Give me your opinion: should I turn this on now or wait?",
        ],
    },
}


def _jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def build() -> list[dict]:
    rng = random.Random(SEED)
    excerpts = _jsonl(POOL / "excerpts.jsonl")
    by_id = {e["id"]: e for e in excerpts}
    items: list[dict] = []
    for lang in ("pt", "en"):
        pool = sorted((e for e in excerpts if e["lang"] == lang and 80 <= e["words"] <= 300), key=lambda e: e["id"])
        rng.shuffle(pool)
        picks = iter(pool)
        for kind, phrasings in PHRASINGS[lang].items():
            for i, text in enumerate(phrasings, 1):
                ex = next(picks)
                items.append({
                    "item_id": f"T:{lang}:{kind}:{i}", "family": "T", "lang": lang, "kind": kind,
                    "message": text, "excerpt_ids": [ex["id"]], "excerpts": [ex["text"]],
                })
    ans = sorted((it for it in _jsonl(POOL / "items.jsonl") if it["family"] == "ANS"), key=lambda it: it["item_id"])
    for lang in ("pt", "en"):
        part = [it for it in ans if it["lang"] == lang]
        for it in rng.sample(part, 15):
            items.append({
                "item_id": f"C:{it['item_id']}", "family": "C", "lang": lang, "kind": "answerable_question",
                "message": it["question"], "excerpt_ids": list(it["excerpt_ids"]),
                "excerpts": [by_id[x]["text"] for x in it["excerpt_ids"]],
            })
    return items


def dump(items: list[dict]) -> str:
    return "".join(json.dumps(it, ensure_ascii=False, sort_keys=True) + "\n" for it in items)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    body = dump(build())
    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
    if args.check:
        on_disk = OUT.read_bytes().decode("utf-8") if OUT.exists() else ""
        ok = on_disk == body
        print(("OK " if ok else "DIFFERS ") + digest)
        return 0 if ok else 1
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_bytes(body.encode("utf-8"))
    print(f"{body.count(chr(10))} items, sha256 {digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
