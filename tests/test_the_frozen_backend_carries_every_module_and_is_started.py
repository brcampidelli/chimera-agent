"""The desktop app's frozen backend carries every chimera module, and the release starts it.

v0.68.0 shipped a desktop app whose backend died at start on every machine:
`ModuleNotFoundError: No module named 'chimera.cli.commands.apilogs'`. `chimera/cli/main.py` imports
the command areas BY NAME (`importlib.import_module`), PyInstaller follows only import statements,
and that area — added in #925 — was imported nowhere else, so the freeze left it out. The release
pipeline's one probe of the binary was `--version`, which `chimera_backend.py` answers before the CLI
is imported, so it passed.

Pinned here: the freeze collects every submodule of the package, and the release starts the frozen
backend and requires an HTTP answer from it.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "apps" / "desktop" / "src-tauri" / "build_sidecar.py"
WORKFLOW = ROOT / ".github" / "workflows" / "desktop-release.yml"


def test_the_freeze_collects_every_submodule_of_the_package() -> None:
    text = BUILD.read_text(encoding="utf-8")
    assert re.search(r'"--collect-submodules",\s*"chimera"', text), (
        "build_sidecar.py must pass --collect-submodules chimera: the package imports modules by name"
    )


def test_the_package_does_import_modules_by_name() -> None:
    # Why the rule above exists, checked: if the CLI stopped importing its areas by name this test
    # would say so, and the rule could be reconsidered instead of kept on faith.
    main = (ROOT / "chimera" / "cli" / "main.py").read_text(encoding="utf-8")
    assert "importlib.import_module" in main and '"apilogs"' in main


def test_the_release_starts_the_frozen_backend_and_needs_an_answer() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    step = text.split("- name: The frozen backend starts and answers", 1)
    assert len(step) == 2, "the release no longer starts the frozen backend"
    body = step[1].split("\n      - name:", 1)[0]
    assert "--emit-port-file" in body and "/api/health" in body
    assert "exit 1" in body, "the start probe must fail the job, not only log"
