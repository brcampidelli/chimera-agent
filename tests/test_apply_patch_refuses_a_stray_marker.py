"""A stray conflict marker was silently written as replacement text.
Refusing malformed hunks is safer than guessing which marker the model intended."""

from chimera.tools.edit import ApplyPatchTool


def test_refuses_a_second_separator_without_changing_the_file(tmp_path):
    target = tmp_path / "example.py"
    original = b"a = 1\nb = 2\n"
    target.write_bytes(original)
    patch = """<<<<<<< SEARCH
b = 2
=======
b = 3
=======
>>>>>>> REPLACE"""

    result = ApplyPatchTool(workspace=tmp_path).run(path="example.py", patch=patch)

    assert result.startswith("error:")
    assert target.read_bytes() == original


def test_refuses_a_search_marker_inside_the_replace_body_without_changing_the_file(tmp_path):
    target = tmp_path / "example.py"
    original = b"a = 1\nb = 2\n"
    target.write_bytes(original)
    patch = """<<<<<<< SEARCH
b = 2
=======
b = 3
<<<<<<< SEARCH
>>>>>>> REPLACE"""

    result = ApplyPatchTool(workspace=tmp_path).run(path="example.py", patch=patch)

    assert result.startswith("error:")
    assert target.read_bytes() == original


def test_applies_a_normal_single_hunk(tmp_path):
    target = tmp_path / "example.py"
    target.write_bytes(b"a = 1\nb = 2\n")
    patch = """<<<<<<< SEARCH
b = 2
=======
b = 3
>>>>>>> REPLACE"""

    result = ApplyPatchTool(workspace=tmp_path).run(path="example.py", patch=patch)

    assert result.startswith("applied")
    assert target.read_bytes() == b"a = 1\nb = 3\n"
