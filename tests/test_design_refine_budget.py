"""No network: exercise the spend guard with fake completions in an isolated interpreter."""
from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path


def test_spend_guard_serializes_and_preserves_unknown_cost(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    script = textwrap.dedent("""
        import json
        import sys
        from concurrent.futures import ThreadPoolExecutor
        from pathlib import Path
        from types import SimpleNamespace
        from unittest.mock import patch

        import litellm
        from bench.design_refine import run

        out = Path(sys.argv[1])
        run.RESULTS = out
        out.mkdir()
        (out / "spend-v2.json").write_text(json.dumps({"usd": 3.99, "historical_usd": 0.0, "accounting": "strict-v1"}), encoding="utf-8")
        model = run.MODELS["ds"]
        kwargs = {"model": model, "max_tokens": 40000}
        messages = [{"role": "user", "content": "fixture"}]
        calls = []
        def complete(*args, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(model=model, prompt_tokens=0, completion_tokens=30000, failed_attempts=[])
        class Gateway:
            def complete(self, messages, **kwargs):
                return litellm.completion(messages=messages, **kwargs)
        def one(_):
            try:
                run._guarded_complete(Gateway(), messages, kwargs, model)
                return True
            except RuntimeError:
                return False
        with patch("chimera.orchestration.budget.worst_case_usd", return_value=.2), patch("chimera.orchestration.receipts.price_delegation", return_value=.02), patch.object(litellm, "completion", complete):
            with ThreadPoolExecutor(max_workers=4) as pool:
                outcomes = list(pool.map(one, range(4)))
        assert sum(outcomes) == 1
        assert len(calls) == 1 and calls[0]["num_retries"] == 0
        assert abs(run.spent() - 4.01) < 1e-9  # catalog price exceeds the historical bench price
        assert not (out / "pending-call.json").exists()

        (out / "spend-v2.json").write_text(json.dumps({"usd": 0.0, "historical_usd": 0.0, "accounting": "strict-v1"}), encoding="utf-8")
        calls.clear()
        def fail(*args, **kwargs):
            calls.append(kwargs)
            raise TimeoutError("offline fake")
        with patch("chimera.orchestration.budget.worst_case_usd", return_value=.2), patch.object(litellm, "completion", fail):
            assert not one(0)
            assert (out / "pending-call.json").exists()
            assert not one(0)
        assert len(calls) == 1
        assert run.spent() == 0.0

        # Unknown/too-large reservations refuse before a model call or pending marker.
        (out / "pending-call.json").unlink()
        (out / "spend-v2.json").write_text(json.dumps({"usd": 3.99, "historical_usd": 0.0, "accounting": "strict-v1"}), encoding="utf-8")
        calls.clear()
        with patch("chimera.orchestration.budget.worst_case_usd", return_value=1.1), patch.object(litellm, "completion", complete):
            assert not one(0)
        assert not calls
        assert not (out / "pending-call.json").exists()

        from chimera.core.filelock import try_hold
        held = try_hold(out / "spend")
        assert held is not None and held.real
        try:
            with patch("chimera.orchestration.budget.worst_case_usd", return_value=.2), patch("chimera.orchestration.receipts.price_delegation", return_value=.02), patch.object(litellm, "completion", complete):
                assert not one(0)
            assert not calls
        finally:
            held.release()

        # The historical ledger is never silently treated as reconciled or overwritten.
        (out / "spend-v2.json").unlink()
        old = out / "spend.json"
        old.write_text(json.dumps({"usd": 2.8}), encoding="utf-8")
        before = old.read_bytes()
        calls.clear()
        with patch("chimera.orchestration.budget.worst_case_usd", return_value=.2), patch.object(litellm, "completion", complete):
            assert not one(0)
        assert not calls and old.read_bytes() == before
        (out / "spend-v2.json").write_text(json.dumps({"usd": 2.8, "historical_usd": 2.8, "accounting": "strict-v1"}), encoding="utf-8")
        with patch("chimera.orchestration.budget.worst_case_usd", return_value=.2), patch("chimera.orchestration.receipts.price_delegation", return_value=.02), patch.object(litellm, "completion", complete):
            assert one(0)
        assert abs(run.spent() - 2.82) < 1e-9 and old.read_bytes() == before
    """)
    result = subprocess.run([sys.executable, "-c", script, str(tmp_path / "results")],
                            cwd=root, capture_output=True, text=True, timeout=90, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
