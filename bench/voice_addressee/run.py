"""S30-49 spoken-path measurement. The implementation task must not run this module.

    uv run python -m bench.voice_addressee.run --run --model qwen3:4b

Only Ollama calls are made. Tool definitions are inert detection probes: this harness records the
model's requested call and never executes a tool or accesses a workspace.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from bench.voice_addressee.harness import evaluate, load_corpus, summarize  # noqa: E402
from chimera.api.code_api import SPOKEN_NOTE  # noqa: E402
from chimera.decisions.contract import Reading  # noqa: E402
from chimera.decisions.local import LocalLogprobBackend  # noqa: E402
from chimera.decisions.voice_addressee import ADDRESSEE  # noqa: E402

OUT = Path(__file__).with_name("results") / "voice_addressee.jsonl"
TOOLS = [
    {"type": "function", "function": {"name": "inspect_workspace", "description": "Inspect a file in the current coding workspace.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}},
    {"type": "function", "function": {"name": "edit_workspace", "description": "Change a file in the current coding workspace.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]}}},
]


class OllamaMeasurementBackend:
    """Local model adapter; its tool calls are recorded, never executed."""

    def __init__(self, model: str, base_url: str = "http://127.0.0.1:11434", timeout: float = 180) -> None:
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.decisions = LocalLogprobBackend(self.base_url, model)

    def ask_choice(self, transcript: str) -> Reading:
        return self.decisions.ask(transcript, ADDRESSEE)

    def spoken_turn(self, transcript: str):
        started = time.perf_counter()
        response = httpx.post(
            f"{self.base_url}/api/chat",
            json={
                "model": self.model,
                # qwen3 otherwise reasons first: `content` comes back empty and the text lands in
                # `thinking`, which would read as "stayed silent" on every not_for_me row.
                "think": False,
                "stream": False,
                "tools": TOOLS,
                "messages": [
                    {"role": "system", "content": "You are a coding assistant. Use tools when a request to work is addressed to you. For other speech, answer briefly for the ear.\n\n" + SPOKEN_NOTE},
                    {"role": "user", "content": transcript},
                ],
                "options": {"temperature": 0},
            },
            timeout=self.timeout,
        )
        response.raise_for_status()
        message = response.json().get("message", {})
        tool_calls = message.get("tool_calls") or []
        answer = str(message.get("content") or "")
        # On this Ollama `think: false` still lets qwen3 reason, inline, closed by `</think>`; the
        # spoken answer is what follows it. Counting the reasoning would turn every row into a
        # "long answer" (amendment 2026-10-06).
        if "</think>" in answer:
            answer = answer.split("</think>", 1)[1].strip()
        elif "<think>" in answer:
            answer = ""
        # Keep timing available in the row while never invoking the model-requested tools.
        self.last_seconds = time.perf_counter() - started
        from bench.voice_addressee.harness import SpokenOutcome

        return SpokenOutcome(tool_call=bool(tool_calls), answer=answer)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", help="contact local Ollama; omitted means validate corpus only")
    parser.add_argument("--model", default="qwen3:4b")
    parser.add_argument("--ollama", default="http://127.0.0.1:11434")
    parser.add_argument("--output", type=Path, default=OUT)
    args = parser.parse_args()
    items = load_corpus()
    print(f"corpus: {len(items)} rows; labels: " + json.dumps({label: sum(item.label == label for item in items) for label in ("not_for_me", "for_me")}))
    if not args.run:
        print("No model contacted. Pass --run to begin the pre-registered local measurement.")
        return

    backend = OllamaMeasurementBackend(args.model, args.ollama)
    rows = evaluate(backend, items)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
    print(json.dumps({"model": args.model, "output": str(args.output), "summary": summarize(rows)}, indent=2))


if __name__ == "__main__":
    main()
