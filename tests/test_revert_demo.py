"""The verify-or-revert example must actually catch and revert a bad change (shipped artifact)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_DEMO = Path(__file__).resolve().parent.parent / "examples" / "revert_demo" / "demo.py"


def _load_demo():  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location("revert_demo", _DEMO)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclass resolution needs the module registered before exec
    spec.loader.exec_module(module)
    return module


def test_demo_detects_and_reverts_the_regression() -> None:
    demo = _load_demo()
    receipt = demo.run_demo(verbose=False)
    assert receipt.before_passed is True  # baseline is green
    assert receipt.after_change_passed is False  # the injected regression is detected
    assert receipt.reverted_files >= 1  # the checkpoint rolled the change back
    assert receipt.after_revert_passed is True  # the good state is restored
    assert receipt.change_rejected is True  # the whole point: the bad change did not survive
