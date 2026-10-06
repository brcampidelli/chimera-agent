"""Keep the optional pet isolated from the agent and its push channels.

The only modules allowed to import ``chimera.pet`` are ``chimera/cli/main.py`` and
``chimera/features.py`` (the pet module itself is naturally not an importer outside this
allowlist). No server or scheduler module may refer to any field in ``chimera.pet.Pet``.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PET_IMPORT_ALLOWLIST = {
    Path("chimera/cli/main.py"),
    Path("chimera/features.py"),
    Path("chimera/pet.py"),
}
PET_STATE_NAMES = {"fullness", "happiness", "energy"}


def _imports_pet(tree: ast.Module) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(alias.name == "chimera.pet" or alias.name.startswith("chimera.pet.") for alias in node.names):
                return True
        elif isinstance(node, ast.ImportFrom):
            if node.module == "chimera.pet":
                return True
            if node.module == "chimera" and any(alias.name == "pet" for alias in node.names):
                return True
    return False


def test_pet_imports_stay_in_the_allowlist_and_state_stays_out_of_push_channels() -> None:
    """Only cli/main.py, features.py, and pet.py may import chimera.pet."""
    offenders: list[str] = []
    for source in sorted((ROOT / "chimera").rglob("*.py")):
        relative = source.relative_to(ROOT)
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=relative.as_posix())
        if _imports_pet(tree) and relative not in PET_IMPORT_ALLOWLIST:
            offenders.append(relative.as_posix())

    assert not offenders, "unexpected chimera.pet import(s): " + ", ".join(offenders)

    push_offenders: list[str] = []
    for package in (ROOT / "chimera" / "server", ROOT / "chimera" / "scheduler"):
        for source in sorted(package.rglob("*.py")):
            relative = source.relative_to(ROOT)
            tree = ast.parse(source.read_text(encoding="utf-8"), filename=relative.as_posix())
            hits = sorted(
                {
                    node.attr
                    for node in ast.walk(tree)
                    if isinstance(node, ast.Attribute) and node.attr in PET_STATE_NAMES
                }
                | {
                    node.id
                    for node in ast.walk(tree)
                    if isinstance(node, ast.Name) and node.id in PET_STATE_NAMES
                }
                | {
                    node.value
                    for node in ast.walk(tree)
                    if isinstance(node, ast.Constant)
                    and isinstance(node.value, str)
                    and node.value in PET_STATE_NAMES
                }
            )
            if hits:
                push_offenders.append(f"{relative.as_posix()} ({', '.join(hits)})")

    assert not push_offenders, "pet state referenced by push-channel module(s): " + ", ".join(push_offenders)
