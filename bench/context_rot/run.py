"""Sweep context length with the task held constant. See `PREREGISTRATION.md`.

    python bench/context_rot/run.py [--repeats 10] [--out rows.json]

A single model call per row, deliberately: an agent loop adds attempts, tool calls and retries, and
every one of those is variance that has nothing to do with length. What is wanted is a fact about the
model, not about the harness around it.
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from chimera.config import get_settings  # noqa: E402
from chimera.core.agent import DEFAULT_SYSTEM_PROMPT  # noqa: E402
from chimera.providers.gateway import LLMGateway, Message  # noqa: E402

LENGTHS = [4_000, 16_000, 64_000, 200_000, 400_000, 786_000]
MODELS = [
    "openrouter/deepseek/deepseek-v4-flash-0731",
    "openrouter/z-ai/glm-5.3-flash",
]

#: Only used to ASSEMBLE the padding; what is reported is always the provider's own count.
#: Calibrated on a smoke run rather than guessed: at 3.6 the 786k target arrived as 703,554, and
#: the point of that target is that it IS the length where `context_budget=0.6` finally fires.
CHARS_PER_TOKEN = 4.05

#: Words, deliberately, and not an opaque identifier. The first version asked the model to echo
#: a dash-separated code after an equals sign, and the answers it produced tripped this
#: repository's secret scanner — correctly, because that is the shape of a credential
#: assignment and a scanner cannot know this one means nothing. The fix is to stop writing that
#: shape into the repository, not to add an allowlist: weakening a security gate so one bench's
#: fixture can pass is a trade nobody would make on purpose.
FACT = "marlin four four zero two"

BRIEF = (
    "Rules for everything you write in this session, and they hold for every later message:\n"
    "  1. Every Python file you produce starts with the exact line '# (c) Bruno 2026'.\n"
    "  2. Every function name you define starts with 'bee_'.\n"
    "  3. You never call print(); return values instead.\n"
    f"Also record this build marker, which I will ask for later: {FACT}\n"
    "Acknowledge and wait."
)

REQUEST = (
    "Write one small Python function that returns the median of a list of numbers. "
    "Reply with the file contents only, in a single ```python block. "
    "After the block, on its own line, write: BUILD MARKER: <the marker I gave you>"
)


def padding(target_tokens: int) -> list[Message]:
    """Plausible prior conversation, built from this repository's own source.

    Real text rather than a repeated filler string: a repeated span is compressible and a model may
    treat it differently from novel content, which would make the sweep measure the filler.
    """
    files = sorted(p for p in (REPO / "chimera").rglob("*.py") if p.stat().st_size > 2_000)
    random.Random(11).shuffle(files)
    want = int(target_tokens * CHARS_PER_TOKEN)
    turns: list[Message] = []
    used = 0
    index = 0
    while used < want and files:
        source = files[index % len(files)]
        index += 1
        try:
            body = source.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        chunk = body[: max(2_000, min(len(body), want - used))]
        used += len(chunk)
        rel = source.relative_to(REPO).as_posix()
        turns.append(Message(role="user", content=f"For reference, here is {rel}:\n\n{chunk}"))
        turns.append(Message(role="assistant", content=f"Noted {rel}."))
    return turns


RULE_1 = re.compile(r"^\s*#\s*\(c\)\s*Bruno\s*2026", re.M)
RULE_2 = re.compile(r"^\s*def\s+(\w+)", re.M)


def score(answer: str) -> tuple[bool, bool, dict[str, bool]]:
    """(rule, fact, per-rule detail). All three rules must hold for RULE to pass."""
    block = answer
    fenced = re.search(r"```(?:python)?\s*(.*?)```", answer, re.S)
    if fenced:
        block = fenced.group(1)
    names = RULE_2.findall(block)
    detail = {
        "header": bool(RULE_1.search(block)),
        "prefix": bool(names) and all(n.startswith("bee_") for n in names),
        "no_print": "print(" not in block,
    }
    return all(detail.values()), FACT in answer, detail


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repeats", type=int, default=10)
    ap.add_argument("--out", default=str(Path(__file__).resolve().parent / "rows.json"))
    args = ap.parse_args()

    # The gateway is still built, and only for its side effect: it is what registers the
    # OpenRouter key and base with litellm. Calling litellm directly below without this would
    # fail on credentials, and that dependency is easier to see written down than discovered.
    LLMGateway(get_settings())
    rows: list[dict] = []
    pads = {n: padding(n) for n in LENGTHS}
    total = len(MODELS) * len(LENGTHS) * args.repeats
    done = 0

    for model in MODELS:
        for length in LENGTHS:
            for rep in range(args.repeats):
                done += 1
                messages = [
                    Message(role="system", content=DEFAULT_SYSTEM_PROMPT),
                    Message(role="user", content=BRIEF),
                    Message(role="assistant", content="Understood. Waiting."),
                    *pads[length],
                    Message(role="user", content=REQUEST),
                ]
                started = time.time()
                row: dict = {
                    "model": model, "target_tokens": length, "rep": rep,
                    "rule": False, "fact": False, "detail": {}, "prompt_tokens": 0,
                    "seconds": 0.0, "error": "", "answer_head": "", "provider": "",
                }
                try:
                    # Straight to litellm here, not through the gateway, for one field the
                    # gateway does not keep: OpenRouter routes a slug to whichever backend it
                    # picks, and `provider` says which one answered. Two runs of this same cell
                    # disagreed by 7/10 against 2/15, and a backend that handles 790k
                    # differently is the first hypothesis a contradiction that size deserves.
                    import litellm

                    raw = litellm.completion(
                        model=model,
                        messages=[{"role": m.role, "content": m.content} for m in messages],
                        temperature=0.0,
                    )
                    payload = raw.model_dump() if hasattr(raw, "model_dump") else dict(raw)
                    row["provider"] = str(payload.get("provider") or "")

                    class _R:  # what the scorer below expects
                        content = (payload["choices"][0]["message"].get("content") or "")
                        prompt_tokens = (payload.get("usage") or {}).get("prompt_tokens", 0)

                    result = _R()
                    answer = result.content or ""
                    rule, fact, detail = score(answer)
                    row.update(
                        rule=rule, fact=fact, detail=detail,
                        # The provider's own count, never the padding estimate: a target of 786k that
                        # arrives as 400k is a different measurement than the one registered.
                        prompt_tokens=int(getattr(result, "prompt_tokens", 0) or 0),
                        answer_head=answer[:400],
                    )
                except Exception as exc:  # noqa: BLE001 — a dead cell is a result, not a crash
                    row["error"] = str(exc)[:220]
                row["seconds"] = round(time.time() - started, 1)
                rows.append(row)
                print(
                    f"  [{done:3}/{total}] {model.split('/')[-1][:24]:24} alvo={length:7} "
                    f"real={row['prompt_tokens']:7} regra={row['rule']!s:5} fato={row['fact']!s:5} "
                    f"{row['provider'][:14]:14} "
                    f"{row['seconds']:5.1f}s" + (f"  ERRO {row['error'][:60]}" if row["error"] else "")
                )
                Path(args.out).write_text(
                    json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8"
                )
    print(f"\n  {len(rows)} linhas em {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
