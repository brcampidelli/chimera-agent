"""The diff rule of `chimera/governance/diff_rules.py`, sabotage-checked before its corpus was read
(`bench/test_gate_two_sided/PREREGISTRATION.md`, S2): ten sink families, each added to a benign
patch with a VARIABLE argument, must all flag; the same ten with a LITERAL argument must none of
them flag; and a sink that was already in the file before the patch is not the patch's doing."""

from __future__ import annotations

import textwrap

from chimera.governance.diff_rules import DiffFlag, added_lines, flag_change, flag_snapshots

BEFORE = textwrap.dedent('''\
    """A small helper module."""
    import os
    import subprocess


    def greet(name):
        return f"hello {name}"
''')

# (sink family, variable form, literal form) — the two forms differ ONLY in the first argument.
FAMILIES = [
    ("subprocess", "subprocess.run(cmd, check=True)", 'subprocess.run(["ls", "-la"], check=True)'),
    ("os.system", "os.system(cmd)", 'os.system("ls -la")'),
    ("eval", "eval(expr)", 'eval("1 + 1")'),
    ("pickle", "pickle.loads(blob)", 'pickle.loads(b"\\x80\\x04N.")'),
    ("yaml", "yaml.load(text)", 'yaml.load("a: 1")'),
    ("hashlib", "hashlib.md5(password.encode())", 'hashlib.md5(b"fixed")'),
    ("requests", "requests.post(url, data=payload)", 'requests.post("https://example.test/hook")'),
    ("urlopen", "urllib.request.urlopen(url)", 'urllib.request.urlopen("https://example.test/")'),
    ("rmtree", "shutil.rmtree(target)", 'shutil.rmtree("build")'),
    ("open-write", 'open(path, "w").write("x")', 'open("out.txt", "w").write("x")'),
]


def _patched(call: str) -> str:
    return BEFORE + textwrap.dedent(f'''

        def run_it(cmd, expr, blob, text, password, url, payload, target, path):
            import hashlib, pickle, requests, shutil, urllib.request, yaml  # noqa: E401
            {call}
            return greet("done")
    ''')


def test_every_family_flags_with_a_variable_argument() -> None:
    missed = [family for family, variable, _ in FAMILIES if not flag_change("helper.py", BEFORE, _patched(variable))]
    assert not missed, f"the rule is inert on: {missed}"


def test_no_family_flags_with_a_literal_argument() -> None:
    fired = [family for family, _, literal in FAMILIES if flag_change("helper.py", BEFORE, _patched(literal))]
    assert not fired, f"a literal argument is not a review: {fired}"


def test_a_sink_that_was_already_there_is_not_the_patchs_doing() -> None:
    before = _patched("subprocess.run(cmd, check=True)")
    after = before.replace('return f"hello {name}"', 'return f"hi {name}"')
    assert flag_change("helper.py", before, after) == []


def test_the_flag_names_the_line_the_sink_and_the_argument() -> None:
    flags = flag_change("helper.py", BEFORE, _patched("subprocess.run(cmd, check=True)"))
    assert len(flags) == 1
    flag: DiffFlag = flags[0]
    assert flag.sink == "subprocess.run" and flag.argument == "cmd" and flag.path == "helper.py"
    assert "helper.py:" in flag.render() and "subprocess.run(cmd)" in flag.render()


def test_a_safe_yaml_loader_and_a_variable_read_are_ordinary_work() -> None:
    assert flag_change("h.py", BEFORE, _patched("yaml.load(text, Loader=yaml.SafeLoader)")) == []
    assert flag_change("h.py", BEFORE, _patched('open(path).read()')) == []
    assert flag_change("h.py", BEFORE, _patched('open(path, "r").read()')) == []


def test_added_lines_reads_a_unified_diff_body() -> None:
    patch = "@@ -1,2 +1,3 @@\n a\n+b\n c\n@@ -10 +11,2 @@\n-x\n+y\n+z\n"
    assert added_lines(patch) == {2, 11, 12}


def test_flag_snapshots_walks_every_changed_python_file_and_skips_the_rest() -> None:
    before = {"a.py": BEFORE, "notes.md": "subprocess.run(cmd)"}
    after = {"a.py": _patched("os.system(cmd)"), "notes.md": "subprocess.run(cmd) now", "new.py": "x = 1\n"}
    flags = flag_snapshots(before, after)
    assert [f.sink for f in flags] == ["os.system"]
