"""`shell=True` appears in no `chimera/**/*.py` outside `chimera/sandbox/` — except where this file
says so, by name, with the reason.

`chimera/sandbox/` is where a command becomes a process, and everything that makes that safe lives
there once: the kernel wrapper, the secret-scrubbed environment, the non-interactive overrides, the
timeout that kills the whole tree, and the `is_isolated()` claim the host-exec gate reads. A
`shell=True` anywhere else is a second place for that history to be forgotten — which is exactly
what `CommandVerifier.verify` was for as long as it existed: a shell string from a repository's
own files, a cron job, a card or a workflow, spawned on the host with none of it.

The exemptions below are listed, not hidden. The test asserts the offenders are *exactly* that
set: a new `shell=True` fails the build, and an exemption whose site no longer spawns a shell fails
it too, so the list cannot go stale in either direction.
"""

from __future__ import annotations

import ast
import pathlib

CHIMERA_DIR = pathlib.Path(__file__).resolve().parents[1] / "chimera"
SANDBOX_DIR = CHIMERA_DIR / "sandbox"

#: Files outside `chimera/sandbox/` that pass ``shell=True`` to a subprocess, and why each may.
EXEMPT: dict[str, str] = {
    "chimera/api/exec_stream.py": (
        "the Runner panel streaming a command a person typed, behind the bearer and the loopback "
        "bind. It takes this path only when get_sandbox() resolves to a plain LocalSandbox "
        "(`type(...) is`, not isinstance, so a kernel wrapper is never stepped around) and spawns "
        "with that sandbox's own _child_env — the streaming twin of LocalSandbox.run, not a way "
        "around it"
    ),
}


def _spawns_a_shell(tree: ast.AST) -> bool:
    """Whether ``tree`` contains a call passing the literal ``shell=True``."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        for keyword in node.keywords:
            value = keyword.value
            if keyword.arg == "shell" and isinstance(value, ast.Constant) and value.value is True:
                return True
    return False


def _files_spawning_a_shell_outside_the_sandbox() -> set[str]:
    found: set[str] = set()
    for path in sorted(CHIMERA_DIR.rglob("*.py")):
        if path.is_relative_to(SANDBOX_DIR):
            continue
        if _spawns_a_shell(ast.parse(path.read_text(encoding="utf-8"))):
            found.add(path.relative_to(CHIMERA_DIR.parent).as_posix())
    return found


def test_no_shell_is_spawned_outside_the_sandbox_package_except_the_ones_named_here() -> None:
    offenders = _files_spawning_a_shell_outside_the_sandbox()

    new = sorted(offenders - set(EXEMPT))
    assert not new, (
        f"shell=True outside chimera/sandbox/: {new} — run the command through get_sandbox() "
        "(see CommandVerifier.verify), or add the file to EXEMPT with the reason it may not"
    )
    stale = sorted(set(EXEMPT) - offenders)
    assert not stale, f"EXEMPT names files that no longer spawn a shell: {stale}"


def test_the_verifier_itself_is_not_an_exemption() -> None:
    """The regression, stated on its own: the verifier is the file this guard was written for, and
    the day it needs an exemption is the day the sleeper channel is open again."""
    assert "chimera/core/verify.py" not in EXEMPT
    assert "chimera/core/verify.py" not in _files_spawning_a_shell_outside_the_sandbox()


def test_the_walk_is_not_vacuous() -> None:
    """Proof the detector can still see an offender, and does not fire on a shell that is a variable
    (that is `LocalSandbox.run`'s seam, and it lives inside the sandbox package)."""
    assert _spawns_a_shell(ast.parse('subprocess.run("true", shell=True)\n')) is True
    assert _spawns_a_shell(ast.parse('subprocess.Popen(argv, shell=use_shell)\n')) is False
    assert _spawns_a_shell(ast.parse('subprocess.run(["true"])\n')) is False


def test_the_sandbox_package_is_where_the_shell_lives() -> None:
    """The exclusion is not an oversight: the package the guard skips must contain the shell it
    exists to centralise, or the guard would be skipping nothing and proving nothing."""
    inside = [
        path.relative_to(CHIMERA_DIR.parent).as_posix()
        for path in sorted(SANDBOX_DIR.glob("*.py"))
        if any(
            isinstance(node, ast.Call) and any(kw.arg == "shell" for kw in node.keywords)
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        )
    ]
    assert "chimera/sandbox/local.py" in inside
