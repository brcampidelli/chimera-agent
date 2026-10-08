"""Eikos-4B's letter readout over a llama.cpp server — see PREREGISTRATION.md (written first).

Runs in its OWN Python environment (transformers for the tokenizer, fastapi, uvicorn), never in
Chimera's:

    python eikos_server.py --checkpoint PATH/TO/Eikos-4B --llama http://127.0.0.1:8091 [--port 8766]

The checkpoint directory is the vendor's snapshot; only its tokenizer, chat template,
``decision_core.py`` (sha256-checked against the repo's ``SHA256SUMS``), ``decision_config.json``
and ``calib.json`` are read here. The weights are served by ``llama-server`` from the Q8_0 GGUF
converted from that same snapshot.

Per question, exactly the vendor's ``LetterAdapter.dist`` path: ``messages(state, question,
options)`` rendered with the checkpoint's chat template (``enable_thinking=False``), tokenized by the
checkpoint's tokenizer, and the **token ids** sent to ``/completion`` (``n_predict`` 1,
``n_probs`` 50). The distribution is the softmax over the option letters, then the vendor's
``temp_for`` with the shipped ``calib.json``. A letter missing from the top 50 gets an equal share of
the remaining mass — the vendor's own rule when its server caps logprobs — and each answer counts
how many letters needed it (``usage.fallback_letters``).

``POST /v1/systemone`` takes the Jev body (``state``, ``questions``; a question may carry its option
order in ``labels``) and returns Jev-shaped answers. ``GET /health`` reports the pins and the
letter-token check (guard 3), which runs before the server accepts a request.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import math
import os
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any

N_PROBS = 50
VENDOR_FILES = ("decision_core.py", "decision_config.json", "calib.json", "tokenizer.json", "tokenizer_config.json",
                "chat_template.jinja")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_vendor(ckpt: Path) -> None:
    sums = {}
    for line in (ckpt / "SHA256SUMS").read_text(encoding="utf-8").splitlines():
        if line.strip():
            digest, name = line.split(maxsplit=1)
            sums[name.strip()] = digest
    for name in VENDOR_FILES:
        if _sha(ckpt / name) != sums.get(name):
            raise SystemExit(f"{name} does not match the vendor's SHA256SUMS")


def _post(url: str, body: dict[str, Any]) -> dict[str, Any]:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=600) as r:
        data: dict[str, Any] = json.loads(r.read())
    return data


class Eikos:
    def __init__(self, ckpt: Path, llama: str) -> None:
        cfg = json.loads((ckpt / "decision_config.json").read_text(encoding="utf-8"))
        os.environ["PROMPT_STYLE"] = cfg["prompt_version"].rsplit("-", 1)[-1]  # as the vendor's local_demo.py
        sys.path.insert(0, str(ckpt))
        self.core = importlib.import_module("decision_core")
        if cfg["prompt_version"] != self.core.PROMPT_VERSION:
            raise SystemExit(f"prompt version {self.core.PROMPT_VERSION} != {cfg['prompt_version']}")
        self.core.set_max_one_pass(cfg.get("max_one_pass"))
        self.calib = json.loads((ckpt / "calib.json").read_text(encoding="utf-8"))
        from transformers import AutoTokenizer

        self.tok = AutoTokenizer.from_pretrained(str(ckpt), local_files_only=True)
        self.llama = llama.rstrip("/")
        self.letter_ids = self._letter_ids()
        self.prompt_version = cfg["prompt_version"]

    def _letter_ids(self) -> list[int]:
        """Guard 3: each letter A–Z is one token in the HF tokenizer and the same one token in the GGUF."""
        ids = []
        for letter in self.core.LETTERS:
            t = self.tok.encode(letter, add_special_tokens=False)
            if len(t) != 1:
                raise SystemExit(f"letter {letter} is {len(t)} tokens in the checkpoint tokenizer")
            served = _post(f"{self.llama}/tokenize", {"content": letter})["tokens"]
            back = _post(f"{self.llama}/detokenize", {"tokens": t})["content"]
            if served != t or back != letter:
                raise SystemExit(f"letter {letter}: HF id {t}, GGUF tokenizes to {served}, detokenizes to {back!r}")
            ids.append(t[0])
        if len(set(ids)) != len(ids):
            raise SystemExit("two letters share a token id")
        return ids

    def text(self, state: Any, question: dict[str, Any], opts: list[tuple[str, str]]) -> str:
        return str(self.tok.apply_chat_template(self.core.messages(state, question, opts), tokenize=False,
                                                add_generation_prompt=True, enable_thinking=False))

    def dist(self, state: Any, question: dict[str, Any], opts: list[tuple[str, str]]) -> tuple[dict[str, float], int, int]:
        if len(opts) > len(self.letter_ids):
            raise ValueError(f"{len(opts)} options exceed the {len(self.letter_ids)} checked letters")
        ids = self.tok(self.text(state, question, opts), add_special_tokens=False)["input_ids"]
        data = _post(f"{self.llama}/completion", {
            "prompt": ids, "n_predict": 1, "n_probs": N_PROBS, "temperature": 0.0,
            "cache_prompt": False, "post_sampling_probs": False,
        })
        top = data["completion_probabilities"][0]["top_logprobs"]
        lp = {int(x["id"]): float(x["logprob"]) for x in top}
        want = self.letter_ids[: len(opts)]
        rest = [i for i in want if i not in lp]
        if rest:
            tail = max(1.0 - sum(math.exp(v) for v in lp.values()), 1e-12)
            lp.update({i: math.log(tail / len(rest)) for i in rest})
        n_tok = len(ids)
        temp = self.core.temp_for(self.calib, 1.0, n_tok, len(opts), question.get("type", "choice"))
        logits = [lp[i] / temp for i in want]
        top_logit = max(logits)
        exps = [math.exp(v - top_logit) for v in logits]
        z = sum(exps)
        return {lab: e / z for (lab, _), e in zip(opts, exps, strict=True)}, n_tok, len(rest)

    def answer(self, state: Any, question: dict[str, Any]) -> tuple[dict[str, Any], int, int]:
        labels = question.get("labels")
        opts = self.core.options_of(question, list(labels) if labels and question.get("type") not in ("noul", "boolean") else None)
        probs, n_tok, fallback = self.dist(state, question, opts)
        best = max(probs, key=lambda k: probs[k])
        kind = question.get("type", "choice")
        if kind in ("noul", "boolean"):
            out = {"type": "noul", "noul": probs["yes"], "probabilities": probs, "confidence": probs[best]}
        elif kind == "score":
            out = {"type": "score", "probabilities": probs, "confidence": probs[best],
                   "score": sum(float(k) * v for k, v in probs.items()) if all(_num(k) for k in probs) else None}
        else:
            out = {"type": "choice", "choice": best, "probabilities": probs, "confidence": probs[best]}
        return out, n_tok, fallback


def _num(s: str) -> bool:
    try:
        float(s)
    except ValueError:
        return False
    return True


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--checkpoint", type=Path, required=True)
    ap.add_argument("--llama", default="http://127.0.0.1:8091")
    ap.add_argument("--port", type=int, default=8766)
    ap.add_argument("--print-prompt", action="store_true", help="print one rendered prompt tail and exit")
    args = ap.parse_args()
    verify_vendor(args.checkpoint)
    eikos = Eikos(args.checkpoint, args.llama)
    print(f"letters checked: A-Z single tokens, ids {eikos.letter_ids[:6]}... · {eikos.prompt_version}", flush=True)
    if args.print_prompt:
        q = {"type": "choice", "instructions": "Pick one.", "criteria": {"a": "first", "b": "second"}}
        print(eikos.text("state text", q, eikos.core.options_of(q))[-600:])
        return

    import uvicorn
    from fastapi import FastAPI, HTTPException

    app = FastAPI()

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {"prompt_version": eikos.prompt_version, "letter_ids": eikos.letter_ids, "n_probs": N_PROBS,
                "calib": eikos.calib}

    @app.post("/v1/systemone")
    def systemone(request: dict[str, Any]) -> dict[str, Any]:
        questions = request.get("questions") or {}
        if not questions:
            raise HTTPException(status_code=422, detail="no questions")
        t0 = time.perf_counter()
        answers, n_in, fallback = {}, 0, 0
        try:
            for qid, q in questions.items():
                answers[qid], n_tok, fb = eikos.answer(request.get("state"), q)
                n_in, fallback = n_in + n_tok, fallback + fb
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {
            "model": "caiovicentino1/Eikos-4B@d06420b (GGUF Q8_0)", "answers": answers,
            "usage": {"input_tokens": n_in, "output_tokens": 0, "fallback_letters": fallback},
            "timing": {"server_ms": round((time.perf_counter() - t0) * 1000, 2)},
        }

    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
