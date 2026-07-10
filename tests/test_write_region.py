"""Tests for the declared write-region (M18-3): the file-writers refuse writes outside it."""

from __future__ import annotations

from pathlib import Path

from chimera.tools.builtin import default_registry
from chimera.tools.edit import ApplyPatchTool, EditFileTool
from chimera.tools.files import WriteFileTool
from chimera.tools.write_region import WriteRegion

# --- WriteRegion ------------------------------------------------------------------------


def test_empty_region_allows_in_workspace_denies_outside(tmp_path: Path) -> None:
    region = WriteRegion([], tmp_path)
    assert region.allows(tmp_path / "anything.py") is True
    assert region.allows(tmp_path.parent / "outside.py") is False


def test_patterns_gate_paths(tmp_path: Path) -> None:
    region = WriteRegion(["src/**", "*.md"], tmp_path)
    assert region.allows(tmp_path / "src" / "app.py") is True
    assert region.allows(tmp_path / "src" / "deep" / "mod.py") is True  # * spans /
    assert region.allows(tmp_path / "README.md") is True
    assert region.allows(tmp_path / "config" / "secrets.py") is False
    err = region.check(tmp_path / "config" / "secrets.py")
    assert err is not None and "outside the declared write-region" in err and "config/secrets.py" in err


# --- tool integration -------------------------------------------------------------------


def test_write_file_refuses_outside_region(tmp_path: Path) -> None:
    tool = WriteFileTool(tmp_path, write_region=WriteRegion(["src/**"], tmp_path))
    refused = tool.run(path="config/secrets.py", content="pwned")
    assert refused.startswith("error:") and "write-region" in refused
    assert not (tmp_path / "config" / "secrets.py").exists()
    ok = tool.run(path="src/app.py", content="clean")
    assert "wrote" in ok and (tmp_path / "src" / "app.py").read_text() == "clean"


def test_edit_file_refuses_outside_region_before_touching(tmp_path: Path) -> None:
    (tmp_path / "evil.py").write_text("original", encoding="utf-8")
    tool = EditFileTool(tmp_path, write_region=WriteRegion(["src/**"], tmp_path))
    refused = tool.run(path="evil.py", old="original", new="hacked")
    assert refused.startswith("error:") and "write-region" in refused
    assert (tmp_path / "evil.py").read_text() == "original"  # untouched


def test_apply_patch_refuses_outside_region(tmp_path: Path) -> None:
    (tmp_path / "evil.py").write_text("a", encoding="utf-8")
    tool = ApplyPatchTool(tmp_path, write_region=WriteRegion(["src/**"], tmp_path))
    patch = "<<<<<<< SEARCH\na\n=======\nb\n>>>>>>> REPLACE"
    refused = tool.run(path="evil.py", patch=patch)
    assert refused.startswith("error:") and "write-region" in refused
    assert (tmp_path / "evil.py").read_text() == "a"


def test_no_region_writes_anywhere_in_workspace(tmp_path: Path) -> None:
    tool = WriteFileTool(tmp_path)  # no region -> today's behaviour
    assert "wrote" in tool.run(path="config/secrets.py", content="x")


def test_default_registry_threads_write_region(tmp_path: Path) -> None:
    reg = default_registry(tmp_path, write_region=WriteRegion(["src/**"], tmp_path))
    # The injected-instruction attack: "also write config/secrets.py" -> refused.
    assert reg.run("write_file", path="config/secrets.py", content="pwned").startswith("error:")
    assert "wrote" in reg.run("write_file", path="src/ok.py", content="clean")
