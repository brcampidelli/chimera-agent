"""Do backends of ONE slug batch tool calls at different rates? See `PREREGISTRATION_backends.md`.

    python bench/parallel_tools/run_backends.py [--runs 120]

Read-only exploration tasks, because that is where the original census found its multi-call batches.
One model slug throughout: the question is about the pool behind it, so varying the slug would
reintroduce exactly the confound under test.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from chimera.config import get_settings  # noqa: E402
from chimera.core import Agent, AgentConfig  # noqa: E402
from chimera.governance.allowlist import restrict_registry  # noqa: E402
from chimera.providers.gateway import LLMGateway  # noqa: E402
from chimera.tools.builtin import default_registry  # noqa: E402

MODEL = "openrouter/deepseek/deepseek-v4-flash-0731"

#: Read-only only. A write turns the run into a different experiment and can fail for reasons that
#: have nothing to do with how many calls the model asked for in a turn.
ALLOWED = ["read_file", "list_dir", "grep", "glob"]

TASKS = [
    "Find where the median is computed in this package and report the file and line.",
    "List every function defined in this package and say which file each is in.",
    "Which module imports the helpers module? Name the files.",
    "Find every place the word 'budget' appears and summarise what each use is for.",
    "Read the two smallest files here and say in one sentence what each does.",
    "What does this package do? Answer from the code, naming the files you read.",
]

FILES = {
    "pkg/__init__.py": "from pkg.helpers import bee_median\nfrom pkg.budget import bee_left\n",
    "pkg/helpers.py": (
        '"""Small numeric helpers."""\n\n\n'
        "def bee_median(numbers):\n"
        "    ordered = sorted(numbers)\n"
        "    n = len(ordered)\n"
        "    if not n:\n        return None\n"
        "    mid = n // 2\n"
        "    return ordered[mid] if n % 2 else (ordered[mid - 1] + ordered[mid]) / 2\n"
    ),
    "pkg/budget.py": (
        '"""Spend accounting."""\n\nfrom pkg.helpers import bee_median\n\n\n'
        "def bee_left(spent, cap):\n"
        '    """How much budget has not been spent."""\n'
        "    return cap - spent\n\n\n"
        "def bee_typical(runs):\n"
        '    """The typical spend across runs."""\n'
        "    return bee_median([r['usd'] for r in runs])\n"
    ),
    "pkg/report.py": (
        '"""Rendering."""\n\nfrom pkg.budget import bee_left\n\n\n'
        "def bee_render(spent, cap):\n"
        "    return f'{bee_left(spent, cap):.2f} left of {cap:.2f}'\n"
    ),
    "README.md": "A tiny package for numeric helpers and spend accounting.\n",
}


class _PinnedGateway:
    """The gateway, with every call routed to one named backend.

    A wrapper rather than a settings flag: pinning is this bench's manipulation, not a mode the
    product should acquire because one measurement wanted it.
    """

    def __init__(self, inner: object, provider: str) -> None:
        self.inner = inner
        self.provider = provider

    def complete(self, messages: object, **kwargs: object) -> object:
        extra = dict(kwargs.pop("extra_body", {}) or {})  # type: ignore[arg-type]
        extra["provider"] = {"order": [self.provider], "allow_fallbacks": False}
        return self.inner.complete(messages, extra_body=extra, **kwargs)  # type: ignore[attr-defined]

    def __getattr__(self, name: str) -> object:
        return getattr(self.inner, name)


def workspace() -> Path:
    root = Path(tempfile.mkdtemp(prefix="backends-"))
    for name, body in FILES.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    return root


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=120)
    ap.add_argument("--pin", default="", help="pin one OpenRouter backend by name")
    ap.add_argument("--out", default=str(Path(__file__).resolve().parent / "backend_rows.json"))
    args = ap.parse_args()

    gateway = LLMGateway(get_settings())
    rows: list[dict] = []

    for index in range(args.runs):
        root = workspace()
        task = TASKS[index % len(TASKS)]
        # Pinned rather than observed: 120 consecutive runs all landed on one backend, so
        # watching the router does not sample the pool. `allow_fallbacks: false` matters — with
        # fallbacks the arm silently becomes whatever answered, which is the confound itself.
        backend = gateway
        if args.pin:
            backend = _PinnedGateway(gateway, args.pin)
        agent = Agent(
            backend,
            restrict_registry(default_registry(root), allow=ALLOWED),
            AgentConfig(model=MODEL, max_steps=6, max_usd=0.05),
        )
        started = time.time()
        try:
            result = agent.run(task)
            steps = [
                {
                    "run": index,
                    "step": record.index,
                    "provider": record.provider,
                    "calls": len(record.tools),
                    "names": sorted(t.name for t in record.tools),
                }
                for record in result.steplog.steps
                if record.tools
            ]
            rows.extend(steps)
            print(
                f"  [{index + 1:3}/{args.runs}] passos-com-ferramenta={len(steps)} "
                f"provedores={sorted({s['provider'] or '(vazio)' for s in steps})} "
                f"usd={result.usd or 0:.4f} {time.time() - started:.0f}s"
            )
        except Exception as exc:  # noqa: BLE001 — a dead run is a row that is absent, not a crash
            print(f"  [{index + 1:3}/{args.runs}] FALHOU {str(exc)[:80]}")
        finally:
            shutil.rmtree(root, ignore_errors=True)
        Path(args.out).write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"\n  {len(rows)} passos com ferramenta em {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
