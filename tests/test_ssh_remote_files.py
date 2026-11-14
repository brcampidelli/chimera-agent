"""The file tools of an SSH conversation work on the REMOTE host, and never leave its folder.

Part B of SSH (piece 1): ``read_file``, ``write_file``, ``edit_file``, ``list_dir``, ``grep`` and ``glob``
keep their names and schemas but act over ssh; the other local file tools still refuse. What these pin,
in the order a mistake would cost most:

* **A path cannot leave the environment's folder.** ``..`` and an absolute path elsewhere are refused
  before ssh is called; a symlink that points out is refused by the host's ``realpath``; and a
  credential file (``.env``, a key, ``.ssh``), by name or through a link, is refused on every tool.
* **Content never rides on a command line.** The script is a fixed string, a path is one quoted
  argument, and the file's bytes are stdin (and base64 on stdout when reading).
* **A write is atomic**: a temp file in the target's folder, then ``mv``; mode and line endings of the
  file that was there survive.
* **The answers are the local tools' answers**: the same sentences for the same mistakes.

Two kinds of fake host, and neither is ssh. :class:`ScriptedHost` answers from a table and records every
call, so the refusals are tested on any machine. :class:`ShellHost` runs the very same fixed scripts with
the local ``sh`` inside a temporary folder, so ``realpath``, ``mv``, ``chmod`` and ``grep`` are the real
ones (skipped where there is no POSIX shell).
"""

from __future__ import annotations

import base64
import os
import shlex
import shutil
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from chimera.sandbox.base import SandboxResult
from chimera.sandbox.ssh import SshSandbox
from chimera.tools import default_registry
from chimera.tools import ssh_remote as remote
from chimera.tools.edit import EditFileTool
from chimera.tools.files import ListDirTool, ReadFileTool, WriteFileTool
from chimera.tools.search import GlobTool, GrepTool
from chimera.tools.ssh_files import (
    LOCAL_FILE_TOOLS,
    REMOTE_FILE_TOOLS,
    RefusedLocalFileTool,
    RemoteFileTool,
    use_remote_files,
)
from chimera.tools.ssh_remote import RemoteFileError, glob_regex, is_secret_name, lexical_target

ROOT = "/srv/app"


class ScriptedHost(SshSandbox):
    """A host that answers from tables. ``links`` maps a path to what ``realpath`` says it is."""

    def __init__(self, root: str = ROOT) -> None:
        super().__init__("web1", root)
        self.calls: list[tuple[str, list[str], bytes | None]] = []
        self.links: dict[str, str] = {}
        self.files: dict[str, bytes] = {}

    def run_script(
        self, script: str, args: Sequence[str] = (), *, stdin: bytes | None = None, timeout: int = 60
    ) -> SandboxResult:
        self.calls.append((script, list(args), stdin))
        if script == remote.RESOLVE:
            root, lexical = args
            real = self.links.get(lexical, lexical)
            data = "\0".join(["OK", root, lexical, real, "f" if lexical in self.files else "o"])
            return SandboxResult(0, _framed(data))
        if script == remote.READ:
            blob = self.files[args[1]]
            return SandboxResult(0, _framed(f"{len(blob)}\n{base64.b64encode(blob).decode()}\n"))
        if script == remote.WRITE:
            assert stdin is not None
            self.files[args[1]] = stdin
            return SandboxResult(0)
        raise AssertionError("an unexpected script was run")

    def scripts_run(self) -> list[str]:
        return [name for script, _, _ in self.calls for name in _NAMES if _NAMES[name] == script]


def _framed(data: str) -> str:
    """What a script prints, as the host's login shell may surround it: a banner before, noise after."""
    return f"Welcome to web1!\n{remote.MARK_BEGIN}{data}{remote.MARK_END}logout\n"


_NAMES = {
    "RESOLVE": remote.RESOLVE, "READ": remote.READ, "WRITE": remote.WRITE,
    "LIST": remote.LIST, "GREP": remote.GREP, "FIND": remote.FIND,
}


def _tool(cls: type[RemoteFileTool], local: Any, host: SshSandbox) -> RemoteFileTool:
    return cls(local, host)


def _read(host: SshSandbox) -> RemoteFileTool:
    from chimera.tools.ssh_files import RemoteReadFile

    return _tool(RemoteReadFile, ReadFileTool(Path.cwd()), host)


def _write(host: SshSandbox) -> RemoteFileTool:
    from chimera.tools.ssh_files import RemoteWriteFile

    return _tool(RemoteWriteFile, WriteFileTool(Path.cwd()), host)


def _edit(host: SshSandbox) -> RemoteFileTool:
    from chimera.tools.ssh_files import RemoteEditFile

    return _tool(RemoteEditFile, EditFileTool(Path.cwd()), host)


# -- a path cannot leave the folder -----------------------------------------------------------------


@pytest.mark.parametrize(
    "path", ["../etc/passwd", "a/../../x", "/etc/passwd", "/srv/app/../other/x", "/srv/apple/x", "//etc/x"]
)
def test_a_path_that_leaves_the_folder_is_refused_before_ssh_is_called(path: str) -> None:
    host = ScriptedHost()
    for tool in (_read(host), _write(host), _edit(host)):
        out = tool.run(path=path, content="x", old="a", new="b")
        assert out.startswith("error:") and "escapes /srv/app" in out, (type(tool).__name__, path)
    assert host.calls == []


def test_a_path_inside_the_folder_is_normalised_not_refused() -> None:
    assert lexical_target(ROOT, "src/../lib/a.py") == "/srv/app/lib/a.py"
    assert lexical_target(ROOT, "/srv/app/lib/a.py") == "/srv/app/lib/a.py"
    assert lexical_target(ROOT, "") == ROOT
    assert lexical_target("/", "etc/hosts") == "/etc/hosts"


def test_a_symlink_that_points_outside_is_refused_by_the_hosts_realpath() -> None:
    host = ScriptedHost()
    host.links["/srv/app/data"] = "/etc/shadow-dir"  # what `realpath` says on the host
    host.links["/srv/app/data/new.txt"] = "/etc/shadow-dir/new.txt"
    for tool in (_read(host), _write(host), _edit(host)):
        out = tool.run(path="data/new.txt", content="x", old="a", new="b")
        assert "resolves outside /srv/app" in out, type(tool).__name__
    # The resolve ran (that is where the link shows); no read or write followed it.
    assert set(host.scripts_run()) == {"RESOLVE"}


def test_a_link_to_a_credential_is_as_refused_as_the_credential() -> None:
    host = ScriptedHost()
    host.links["/srv/app/notes.txt"] = "/srv/app/.ssh/id_rsa"
    out = _read(host).run(path="notes.txt")
    assert "credential file" in out and ".ssh" in out
    assert set(host.scripts_run()) == {"RESOLVE"}


@pytest.mark.parametrize(
    "path", [".env", ".env.production", "deploy/key.pem", "a/.ssh/config", "id_ed25519", "secrets.json", ".aws-not", ".netrc"]
)
def test_a_credential_file_is_refused_by_every_tool_by_name(path: str) -> None:
    secret = path != ".aws-not"
    host = ScriptedHost()
    for tool in (_read(host), _write(host), _edit(host)):
        out = tool.run(path=path, content="x", old="a", new="b")
        assert ("credential file" in out) == secret, (type(tool).__name__, path)
    assert (host.calls == []) == secret


def test_the_denylist_is_the_bridges_and_adds_nothing_that_would_hide_ordinary_files() -> None:
    from chimera.api.bridge_routes import is_secret_file

    names = [
        ".env", ".env.local", "a.pem", "a.key", "a.p12", "a.pfx", "a.jks", "a.keystore", "id_rsa", "id_rsa.pub",
        "id_dsa", "id_ecdsa", "id_ed25519", ".ssh", "credentials", "credentials.json", ".netrc", ".pypirc",
        ".npmrc", ".git-credentials", "secret", "secrets", "secret.yml", "secrets.yml", "README.md",
        "main.py", "environment.md", "keys.txt", "monkey.py", "secretary.txt",
    ]
    for name in names:
        assert is_secret_name(name) == is_secret_file(name), name
    assert is_secret_name(".gnupg") and is_secret_name(".ENV ")  # ours, and Windows' reading of a name


# -- content never rides on a command line ------------------------------------------------------------


def test_content_is_stdin_and_never_part_of_the_scripts_or_the_arguments() -> None:
    host = ScriptedHost()
    payload = "'; rm -rf / #\n$(reboot)\n`id`\n"
    out = _write(host).run(path="notes.txt", content=payload, allow_invalid=True)
    assert out == f"wrote {len(payload)} chars to notes.txt"
    (script, args, stdin) = host.calls[-1]
    assert script == remote.WRITE and stdin == payload.encode("utf-8")
    assert args[:2] == [ROOT, "/srv/app/notes.txt"]
    assert args[2] == str(len(payload.encode("utf-8"))) and len(args[3]) == 64  # the count and the sha256
    assert payload not in script and not any("rm -rf" in a for a in args)


class _Popen:
    """What `subprocess.Popen` is replaced with: records the argv and what was fed to the process."""

    seen: list[dict[str, Any]] = []

    def __init__(self, target: Any, **kwargs: Any) -> None:
        self.target, self.kwargs = target, kwargs
        self.returncode = 0
        self.pid = 1
        Path(target[target.index("-E") + 1]).write_bytes(b"")

    def communicate(self, input: bytes | None = None, timeout: float | None = None) -> tuple[bytes, bytes]:
        _Popen.seen.append({"target": self.target, "input": input, **self.kwargs})
        return b"fine", b""

    def poll(self) -> int:
        return 0


def test_run_script_goes_through_the_ssh_argv_with_quoted_arguments_and_stdin_on_the_pipe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _Popen.seen = []
    monkeypatch.setattr(subprocess, "Popen", _Popen)
    rechecks: list[int] = []
    box = SshSandbox("web1", "/srv/my app")
    box.bind_recheck(lambda: rechecks.append(1))
    box.bind_taint(lambda: True, None)  # a tainted turn: a COMMAND would be refused, a file tool is judged by name
    secret_content = b"top secret ' ; $(x)"
    result = box.run_script("echo \"$1\"; cat", ["a b'c"], stdin=secret_content)
    assert result.exit_code == 0 and result.stdout == "fine"
    (call,) = _Popen.seen
    argv = call["target"]
    assert call["shell"] is False and call["input"] == secret_content
    assert argv[0] == "ssh" and "BatchMode=yes" in argv and "StrictHostKeyChecking=yes" in argv
    assert argv[argv.index("--") + 1] == "web1"
    remote_command = argv[-1]
    assert remote_command == (
        "cd '/srv/my app' || exit 1; sh -c " + shlex.quote("echo \"$1\"; cat") + " sh " + shlex.quote("a b'c")
    )
    assert shlex.split(remote_command.split("; ", 1)[1])[-1] == "a b'c"
    assert b"top secret" not in " ".join(argv).encode()
    assert rechecks == [1]  # the alias is checked again at every call


def test_a_host_removed_mid_turn_stops_the_next_file_call() -> None:
    box = SshSandbox("web1", "/srv/app")

    def recheck() -> None:
        raise ValueError("web1 is no longer on the allowed list")

    box.bind_recheck(recheck)
    result = box.run_script("true")
    assert result.exit_code == 1 and "no longer on the allowed list" in result.stderr


# -- the write is atomic -------------------------------------------------------------------------------


def test_the_write_script_writes_a_temp_file_next_to_the_target_and_only_then_moves_it() -> None:
    lines = remote.WRITE.splitlines()
    index = {key: next(i for i, ln in enumerate(lines) if key in ln) for key in ("mkdir", "mktemp", "cat >", "mv -f")}
    assert index["mkdir"] < index["mktemp"] < index["cat >"] < index["mv -f"]
    assert 'mktemp "$dir/' in remote.WRITE  # the same folder, so the move is a rename on one filesystem
    assert 'cat > "$tmp"' in remote.WRITE and 'cat > "$t"' not in remote.WRITE  # never straight onto the target
    assert 'rm -f -- "$tmp"' in remote.WRITE  # a failed copy or move leaves no temp behind
    # And the guard runs before the first line that changes anything.
    assert remote.WRITE.index("realpath") < remote.WRITE.index("mkdir")


def test_every_script_reads_its_variable_parts_from_positional_parameters() -> None:
    assert {remote.RESOLVE, remote.READ, remote.WRITE, remote.LIST, remote.GREP, remote.FIND} == remote.SCRIPTS
    for script in remote.SCRIPTS:
        assert '"$1"' in script and "$2" in script


# -- the tools keep their identity; the others still refuse -----------------------------------------


def test_the_ported_tools_keep_name_and_schema_and_the_rest_still_refuse(tmp_path: Path) -> None:
    box = SshSandbox("web1", "/srv/app", tmp_path)
    local = default_registry(tmp_path)
    registry = default_registry(tmp_path, host_exec_confirm=None, sandbox=box)
    for name in REMOTE_FILE_TOOLS & set(registry.names()):
        tool = registry.get(name)
        assert isinstance(tool, RemoteFileTool), name
        assert tool.name == local.get(name).name and tool.parameters == local.get(name).parameters, name
        assert "web1" in tool.description and "UNAVAILABLE" not in tool.description, name
    refused = LOCAL_FILE_TOOLS - REMOTE_FILE_TOOLS
    for name in sorted(refused & set(registry.names())):
        assert isinstance(registry.get(name), RefusedLocalFileTool), name
    assert {"execute_code", "read_document"} <= refused
    assert {"apply_patch", "edit_batch"} <= REMOTE_FILE_TOOLS  # ported
    # Running the swap again, as the Design tool's registration does, undoes nothing.
    use_remote_files(registry, box)
    assert isinstance(registry.get("read_file"), RemoteFileTool)


# -- against a real shell: the same scripts, a real realpath, mv, chmod and grep ---------------------

needs_sh = pytest.mark.skipif(
    sys.platform == "win32" or any(shutil.which(p) is None for p in ("sh", "realpath", "base64", "mktemp")),
    reason="needs a POSIX shell with realpath, base64 and mktemp",
)


class ShellHost(SshSandbox):
    """Runs the fixed scripts with the local `sh`, in the folder, as `cd <root> || exit 1; sh -c ...` would."""

    def run_script(
        self, script: str, args: Sequence[str] = (), *, stdin: bytes | None = None, timeout: int = 60
    ) -> SandboxResult:
        done = subprocess.run(
            ["sh", "-c", script, "sh", *args], input=stdin or b"", capture_output=True, cwd=self.remote_path,
            timeout=timeout, check=False,
        )
        return SandboxResult(done.returncode, done.stdout.decode("utf-8", "replace"), done.stderr.decode("utf-8", "replace"))


@pytest.fixture
def proj(tmp_path: Path) -> Path:
    folder = tmp_path / "proj"
    folder.mkdir()
    return folder


def _host(folder: Path) -> ShellHost:
    return ShellHost("web1", str(folder))


def _both(tmp_path: Path, build: Any) -> tuple[Path, Path]:
    """Two identical trees: one for the local tools, one for the remote ones."""
    local, far = tmp_path / "local", tmp_path / "far"
    for folder in (local, far):
        folder.mkdir()
        build(folder)
    return local, far


@needs_sh
def test_a_symlink_that_leaves_the_folder_is_refused_for_real(proj: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "data.txt").write_text("not yours", encoding="utf-8")
    (proj / "link").symlink_to(outside)
    (proj / "filelink").symlink_to(outside / "data.txt")
    host = _host(proj)
    for path in ("link/data.txt", "filelink", "link/new.txt", "link"):
        for tool in (_read(host), _write(host), _edit(host)):
            out = tool.run(path=path, content="pwned", old="not", new="x")
            assert "resolves outside" in out, (path, type(tool).__name__, out)
    assert (outside / "data.txt").read_text(encoding="utf-8") == "not yours"
    assert sorted(p.name for p in outside.iterdir()) == ["data.txt"]  # not even a temp file there


@needs_sh
def test_a_link_inside_the_folder_is_followed_and_a_link_to_a_secret_is_not(proj: Path) -> None:
    (proj / "real.txt").write_text("hello\n", encoding="utf-8")
    (proj / "ok").symlink_to(proj / "real.txt")
    (proj / ".ssh").mkdir()
    (proj / ".ssh" / "id_rsa").write_text("KEY", encoding="utf-8")
    (proj / "innocent.txt").symlink_to(proj / ".ssh" / "id_rsa")
    host = _host(proj)
    assert _read(host).run(path="ok") == "hello\n"
    assert "credential file" in _read(host).run(path="innocent.txt")
    # Writing through a link inside the folder writes the file it points at, and keeps the link.
    _write(host).run(path="ok", content="changed\n")
    assert (proj / "ok").is_symlink() and (proj / "real.txt").read_text(encoding="utf-8") == "changed\n"


@needs_sh
def test_a_write_creates_folders_keeps_mode_and_line_endings_and_leaves_no_temp(proj: Path) -> None:
    host = _host(proj)
    assert _write(host).run(path="deep/er/new.txt", content="a\nb\n") == "wrote 4 chars to deep/er/new.txt"
    assert (proj / "deep/er/new.txt").read_bytes() == b"a\nb\n"
    target = proj / "crlf.txt"
    target.write_bytes(b"one\r\ntwo\r\n")
    target.chmod(0o640)
    _write(host).run(path="crlf.txt", content="x\ny\nz\n")
    assert target.read_bytes() == b"x\r\ny\r\nz\r\n"  # the file's own convention
    assert target.stat().st_mode & 0o777 == 0o640  # and its mode
    new_mode = (proj / "deep/er/new.txt").stat().st_mode & 0o777
    assert new_mode == 0o666 & ~_umask()  # a new file gets what the umask allows, not mktemp's 0600
    leftovers = [p for p in proj.rglob("*") if ".chimera-tmp" in p.name]
    assert leftovers == []


def _umask() -> int:
    current = os.umask(0)
    os.umask(current)
    return current


@needs_sh
def test_a_failed_write_leaves_the_old_file_untouched_and_no_temp(proj: Path) -> None:
    (proj / "keep.txt").write_text("old", encoding="utf-8")
    broken = SandboxResult  # noqa: F841 - keeps the import used on platforms where the test is skipped
    host = _host(proj)
    os.chmod(proj, 0o555)  # the folder cannot take a new file: mktemp fails before anything moves
    try:
        if os.access(proj, os.W_OK):
            pytest.skip("running as a user the folder mode does not bind (root)")
        out = _write(host).run(path="keep.txt", content="new")
    finally:
        os.chmod(proj, 0o755)
    assert out.startswith("error:") and "temporary file" in out
    assert (proj / "keep.txt").read_text(encoding="utf-8") == "old"
    assert [p.name for p in proj.iterdir()] == ["keep.txt"]


@needs_sh
def test_the_write_tool_refuses_what_the_local_one_refuses(tmp_path: Path) -> None:
    local, far = _both(tmp_path, lambda f: (f / "keep.json").write_text("{}", encoding="utf-8"))
    host = _host(far)
    cases = [
        {"path": "bad.py", "content": "def (:\n"},
        {"path": "bad.json", "content": "{nope"},
        {"path": "t.html", "content": r"Utensu00edlios su00e3o su00e1 su00e9"},
        {"path": "bad.py", "content": "def (:\n", "allow_invalid": True},
    ]
    for case in cases:
        want = WriteFileTool(local).run(**case)
        got = _write(host).run(**case)
        assert got.replace(str(local), "").replace(str(far), "") == want.replace(str(local), "").replace(str(far), ""), case


@needs_sh
def test_a_write_onto_a_folder_is_an_error_and_changes_nothing(proj: Path) -> None:
    (proj / "dir").mkdir()
    out = _write(_host(proj)).run(path="dir", content="x")
    assert "that path is a folder" in out and (proj / "dir").is_dir()


@needs_sh
def test_edit_file_answers_exactly_as_the_local_tool_does(tmp_path: Path) -> None:
    def build(folder: Path) -> None:
        (folder / "a.txt").write_bytes(b"one\r\ntwo\r\ntwo\r\n")
        (folder / "b.bin").write_bytes(b"\xff\xfe\x00bad")
        (folder / "c.txt").write_text("alpha\nbeta\n", encoding="utf-8")

    local, far = _both(tmp_path, build)
    host = _host(far)
    cases = [
        {"path": "a.txt", "old": "two", "new": "2"},  # ambiguous
        {"path": "a.txt", "old": "two", "new": "2", "replace_all": True},
        {"path": "a.txt", "old": "nothing", "new": "x"},  # not found
        {"path": "a.txt", "old": "", "new": "x"},
        {"path": "a.txt", "old": "one", "new": "one"},
        {"path": "missing.txt", "old": "a", "new": "b"},
        {"path": "b.bin", "old": "a", "new": "b"},
        {"path": "c.txt", "old": "beta", "new": "gamma"},
    ]
    for case in cases:
        assert _edit(host).run(**case) == EditFileTool(local).run(**case), case
    assert (far / "a.txt").read_bytes() == (local / "a.txt").read_bytes() == b"one\r\n2\r\n2\r\n"
    assert (far / "c.txt").read_text(encoding="utf-8") == (local / "c.txt").read_text(encoding="utf-8")


@needs_sh
def test_read_file_answers_exactly_as_the_local_tool_does(tmp_path: Path) -> None:
    body = "".join(f"line {n}\n" for n in range(1, 4001))

    def build(folder: Path) -> None:
        (folder / "big.txt").write_text(body, encoding="utf-8")
        (folder / "small.txt").write_text("a\r\nb\r\n", encoding="utf-8", newline="")
        (folder / "latin.txt").write_bytes("café".encode("latin-1"))

    local, far = _both(tmp_path, build)
    host = _host(far)
    cases: list[dict[str, Any]] = [
        {"path": "big.txt"},
        {"path": "big.txt", "start_line": 100},
        {"path": "big.txt", "start_line": 10, "max_lines": 5},
        {"path": "big.txt", "start_line": 9999},
        {"path": "big.txt", "start_line": 0},
        {"path": "small.txt"},
        {"path": "latin.txt"},
        {"path": "nope.txt"},
    ]
    for case in cases:
        assert _read(host).run(**case) == ReadFileTool(local).run(**case), case


@needs_sh
def test_a_file_over_the_ceiling_is_refused_with_a_sentence(proj: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (proj / "big.bin").write_bytes(b"x" * 300)
    monkeypatch.setattr(remote, "MAX_READ_BYTES", 100)
    assert "MB; read a part of it" in _read(_host(proj)).run(path="big.bin")


@needs_sh
def test_list_dir_grep_and_glob_answer_as_the_local_tools_do_and_leave_secrets_out(tmp_path: Path) -> None:
    def build(folder: Path) -> None:
        (folder / "src" / "pkg").mkdir(parents=True)
        (folder / "node_modules" / "dep").mkdir(parents=True)
        (folder / ".ssh").mkdir()
        (folder / "a.py").write_text("import os\nprint('hello')\n", encoding="utf-8")
        (folder / "src" / "b.py").write_text("print('hello again')\n\nx = 1\n", encoding="utf-8")
        (folder / "src" / "pkg" / "c.txt").write_text("hello deep\n", encoding="utf-8")
        (folder / "node_modules" / "dep" / "d.py").write_text("print('hello')\n", encoding="utf-8")
        (folder / ".env").write_text("TOKEN=hello\n", encoding="utf-8")
        (folder / ".ssh" / "id_rsa").write_text("hello KEY\n", encoding="utf-8")
        (folder / "tls.pem").write_text("hello PEM\n", encoding="utf-8")

    local, far = _both(tmp_path, build)
    host = _host(far)
    from chimera.tools.ssh_files import RemoteGlob, RemoteGrep, RemoteListDir

    listing = RemoteListDir(ListDirTool(far), host).run(path=".")
    assert listing == "a.py\nnode_modules/\nsrc/"  # no .env, no .ssh, no tls.pem — not even by name
    assert RemoteListDir(ListDirTool(far), host).run(path="src") == "b.py\npkg/"
    assert RemoteListDir(ListDirTool(far), host).run(path="nope") == "error: not a directory: nope"
    assert RemoteListDir(ListDirTool(far), host).run(path="a.py") == "error: not a directory: a.py"

    grep = RemoteGrep(GrepTool(far), host)
    got = grep.run(pattern="hello")
    assert got.splitlines() == [
        "a.py:2: print('hello')",
        "src/b.py:1: print('hello again')",
        "src/pkg/c.txt:1: hello deep",
    ]
    for line in got.splitlines():
        assert line in GrepTool(local).run(pattern="hello").replace(str(local), "")
    assert "TOKEN" not in got and "KEY" not in got and "PEM" not in got  # never READ, not just never shown
    assert grep.run(pattern="hello", glob="*.py") == "a.py:2: print('hello')\nsrc/b.py:1: print('hello again')"
    assert grep.run(pattern="hello", path="src") == "src/b.py:1: print('hello again')\nsrc/pkg/c.txt:1: hello deep"
    assert grep.run(pattern="x = 1", path="src/b.py") == "src/b.py:3: x = 1"
    assert grep.run(pattern="zzz") == "no matches"
    assert grep.run(pattern="hello", max_results=1).endswith("... [stopped at 1 matches]")
    assert grep.run(pattern="(").startswith("error: invalid regex")
    assert grep.run(pattern="hello", path="missing") == "error: no such file or directory: missing"
    assert grep.run(pattern="hello", path=".env").startswith("error:") and "credential" in grep.run(pattern="x", path=".env")

    glob = RemoteGlob(GlobTool(far), host)
    assert glob.run(pattern="**/*.py").splitlines() == ["a.py", "src/b.py"] == GlobTool(local).run(pattern="**/*.py").splitlines()
    assert glob.run(pattern="*.py") == "a.py"
    assert glob.run(pattern="src/**/*.txt") == "src/pkg/c.txt"
    assert glob.run(pattern="**/*.pem") == "no files match" == GlobTool(local).run(pattern="**/*.pem").replace("tls.pem", "no files match")
    assert glob.run(pattern="../*").startswith("error: invalid glob pattern")
    assert glob.run(pattern="/etc/*").startswith("error: invalid glob pattern")


@needs_sh
def test_a_project_that_lives_in_a_folder_named_like_an_ignored_one_is_still_searched(tmp_path: Path) -> None:
    folder = tmp_path / "build"  # `--exclude-dir=build` would skip a command-line folder of that name
    folder.mkdir()
    (folder / "x.txt").write_text("needle\n", encoding="utf-8")
    from chimera.tools.ssh_files import RemoteGlob, RemoteGrep

    host = _host(folder)
    assert RemoteGrep(GrepTool(folder), host).run(pattern="needle") == "x.txt:1: needle"
    assert RemoteGlob(GlobTool(folder), host).run(pattern="*.txt") == "x.txt"


# -- the glob translation, with no host at all --------------------------------------------------------


@pytest.mark.parametrize(
    ("pattern", "path", "matches"),
    [
        ("*.py", "a.py", True),
        ("*.py", "src/a.py", False),
        ("**/*.py", "a.py", True),
        ("**/*.py", "a/b/c.py", True),
        ("src/**/test_*.py", "src/x/y/test_a.py", True),
        ("src/**/test_*.py", "lib/test_a.py", False),
        ("a?.txt", "ab.txt", True),
        ("a?.txt", "a/.txt", False),
        ("[ab].txt", "a.txt", True),
        ("[!ab].txt", "a.txt", False),
        ("*.tar.gz", "x.tar.gz", True),
        ("**", "a.py", False),
        ("src/*", "src/a.py", True),
        ("src/*", "src/d/a.py", False),
        ("a+b(1).txt", "a+b(1).txt", True),
    ],
)
def test_the_glob_pattern_reads_as_pathlib_reads_it(pattern: str, path: str, matches: bool) -> None:
    assert (glob_regex(pattern).fullmatch(path) is not None) is matches


@pytest.mark.parametrize("pattern", ["", "/etc/*", "../x", "a/../../b"])
def test_a_glob_that_is_absolute_or_climbs_is_refused(pattern: str) -> None:
    with pytest.raises(RemoteFileError):
        glob_regex(pattern)
