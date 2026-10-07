"""Three-arm harness. The backend is injected; tests use a fake and no model is contacted here."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Protocol


class Backend(Protocol):
    def reply(self, prompt: str, *, arm: str, tool_enabled: bool) -> tuple[str, list[dict[str, object]]]: ...


def run(requests: list[dict[str, str]], backend: Backend) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for request in requests:
        for arm in ("A", "B", "C"):
            answer, events = backend.reply(
                request["request"], arm=arm, tool_enabled=arm == "C"
            )
            rows.append({"request_id": request["id"], "lang": request["lang"], "family": request["family"], "arm": arm, "answer": answer, "events": events})
    return rows


def load_requests(path: Path) -> list[dict[str, str]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True)
    parser.add_argument("--requests", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.parse_args()
    parser.error("No production backend is implemented; invoke only with an explicitly configured local Ollama backend.")


if __name__ == "__main__":
    main()
