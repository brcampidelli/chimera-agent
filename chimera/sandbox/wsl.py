"""The WSL execution environment: commands run in a Linux distro, on the same disk and the same host.

**This is not isolation, and nothing here pretends it is.** WSL shares the Windows user, the disk
(every drive appears under ``/mnt/<letter>``), the network and, through interop, can start Windows
programs. A command that runs here can do to the machine whatever the same command can do through
``cmd.exe``. So :meth:`WslSandbox.is_isolated` is False, the host-exec gate (``CHIMERA_HOST_EXEC``)
and the Manual / Accept-edits modes treat it as the host, and the environment never loosens anything
the local one would not. What it buys is the right toolchain (``bash``, ``apt``, the project's Linux
build), not a boundary.

**The files stay on the Windows disk.** ``read_file`` / ``write_file`` / ``edit_file`` and the
workspace guard keep working on the local path; the distro sees the same folder through ``/mnt/c``.
No file is copied, and nothing is edited "inside" the distro.

What this module owns, and why it is small:

* the path translation (``C:\\x`` -> ``/mnt/c/x``), which is where the one real risk lives: ``/mnt/c``
  is the whole drive, so a translation that followed ``..``, a junction or a UNC path out of the
  workspace would hand the agent a working directory the workspace guard never approved. It refuses
  instead (:class:`WslPathError`);
* the argv (``wsl.exe [-d distro] --cd <path> --exec bash -lc <command>``), always a list, never
  ``shell=True`` on the Windows side — the command is one argument, and the only shell that parses
  it is the distro's ``bash``;
* the distro list, for the screen that creates the environment.

Why ``--exec``: without it ``wsl.exe`` hands the command line to the distro's DEFAULT shell, which
expands ``$``, backticks and backslashes, and only then starts ``bash -lc`` — which expands them a
second time. The command the host-exec gate read and the one bash runs would differ. ``--exec``
starts ``bash`` directly, with the command as its single argument, so the only expansion is bash's.

The fences that guard Chimera's own files by path (approval queue, audit log, hooks, ``.env``)
resolve paths with the host's rules, where ``/mnt/c/...`` is a folder that does not exist; the turn
reads a WSL command through :func:`windows_view` as well (``chimera.tools.workspace``).

The process itself (timeout, killing the tree, the scrubbed environment, the decoding) is
:class:`~chimera.sandbox.local.LocalSandbox`'s, reused rather than copied: each of those has its own
history of bugs. A limit worth knowing: killing ``wsl.exe`` on a timeout ends the Windows side; what
the distro does with the process it was running is the distro's (it normally gets a hangup).
"""

from __future__ import annotations

import ntpath
import os
import posixpath
import re
import subprocess
from pathlib import Path, PureWindowsPath

from chimera.proc.stdio import no_window
from chimera.sandbox.base import SandboxResult

#: The program. Resolved by the OS from ``System32``; there is no setting for it on purpose.
WSL_EXE = "wsl.exe"

#: Where a Windows drive appears in a default distro (``/etc/wsl.conf`` can change the automount
#: root; a distro that did is not supported, and the command fails loudly instead of in the wrong
#: folder).
MNT_ROOT = "/mnt"

#: A distro name as ``wsl -l -q`` prints them. The leading character cannot be ``-``: the name is an
#: argv element after ``-d`` and a value that looked like a flag would be read as one.
_DISTRO = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")

_LIST_TIMEOUT = 10


class WslPathError(ValueError):
    """A working directory that cannot be handed to the distro without leaving the workspace."""


def wsl_supported() -> bool:
    """WSL exists on Windows only. A seam for the tests, which run on every platform."""
    return os.name == "nt"


def valid_distro(name: str) -> bool:
    """Whether ``name`` is an acceptable distro name (empty is not: "the default" is an absence)."""
    return bool(_DISTRO.fullmatch(name))


def to_wsl_path(path: Path | str, workspace: Path | str) -> str:
    """``path`` as the distro sees it, or :class:`WslPathError` if it is not inside ``workspace``.

    Both sides are resolved first (on Windows, where the answer is the real folder), so a symlink or
    a junction that points out of the workspace fails the containment test in :func:`translate`
    instead of being walked after the translation. ``..`` is folded by the same step.
    """
    return translate(_real(path), _real(workspace))


def _real(path: Path | str) -> str:
    text = str(path)
    if os.name != "nt":
        return text
    try:
        return str(Path(text).resolve(strict=False))
    except (OSError, ValueError, RuntimeError) as exc:
        raise WslPathError("the working directory could not be resolved") from exc


def translate(target: str, workspace: str) -> str:
    """The pure half of :func:`to_wsl_path`, on Windows path semantics whatever the host OS is.

    The workspace must sit on a lettered drive: a UNC share, an extended-length prefix and a
    drive-relative ``C:x`` have no ``/mnt`` spelling, so they are refused
    rather than guessed at. ``target`` must be the workspace or a descendant of it; ``..`` is folded
    before the test, and a different drive is a different root, so it fails the same test.
    """
    root = PureWindowsPath(ntpath.normpath(workspace))
    there = PureWindowsPath(ntpath.normpath(target))
    match = re.fullmatch(r"([A-Za-z]):\\", root.anchor)
    if match is None:
        raise WslPathError("only a workspace on a lettered drive can be reached through /mnt")
    if there != root and not there.is_relative_to(root):
        raise WslPathError("the working directory is outside the workspace")
    # The drive letter of the TARGET, as spelled by the workspace the test above matched it to.
    return "/".join([MNT_ROOT, match.group(1).lower(), *there.parts[1:]])


#: What separates the words of a shell line, kept so the line can be put back together.
_WORD_SPLIT = re.compile(r"(\s+|[;|&<>()`'\",=+\[\]{}]+)")
_MNT_TARGET = re.compile(r"/mnt/([A-Za-z])(?:/(.*))?")


def drive_root(letter: str) -> str:
    """Where drive ``letter`` is on THIS host, with a trailing slash. A seam: tests on any OS point it at a folder."""
    return f"{letter.upper()}:/"


def wsl_cwd_of(cwd: Path | str) -> str:
    """The distro's spelling of ``cwd`` (``/mnt/c/...``), or "" when it has none (not a lettered drive)."""
    try:
        return translate(str(cwd), str(cwd))
    except WslPathError:
        return ""


def _posix_abs(token: str, cur: str, *, directory: bool = False) -> str | None:
    """``token`` as an absolute POSIX path as the distro resolves it, or None when it names no path.

    ``//``, ``/./`` and ``..`` are folded the way the kernel does (``/usr/../mnt/c``, ``/mnt/../mnt/c``
    and ``/mnt//c`` are all ``/mnt/c``), a relative path is read against ``cur`` (the command's folder in the
    distro), and ``..`` past the root stays at the root. ``directory``: the word IS a folder being entered
    (the target of ``cd``, the value of ``-C``), so a bare ``Users`` is a path too and not just a word.
    """
    if token.startswith("/"):
        joined = token
    elif cur and (directory or "/" in token or token in (".", "..")):
        joined = f"{cur}/{token}"
    else:
        return None
    return posixpath.normpath(re.sub(r"^/+", "/", joined))


#: Options that enter a folder for the rest of ONE command (``env -C d cmd``, ``tar -C d``, ``git -C d``,
#: ``make -C d``, ``--directory d``, ``--cd d``), read like a ``cd`` that ends with the command.
_ENTERS_FOLDER = frozenset({"-C", "--directory", "--cd"})
_ENDS_COMMAND = re.compile(r"[;|&\n()]")


def _on_drive(path: str) -> str | None:
    """``path`` (absolute, folded) on a ``/mnt/<letter>`` drive, as this host spells it, else None."""
    match = _MNT_TARGET.fullmatch(path)
    if match is None:
        return None
    root, rest = drive_root(match.group(1)), match.group(2) or ""
    return f"{root.rstrip('/')}/{rest}" if rest else root


def windows_view(text: str, cwd: str = "", *, follow_cd: bool = True) -> str:
    """``text`` with every word that is a path on a ``/mnt/<letter>`` drive spelled as the host spells it.

    For the fences that guard Chimera's own files by path (the approval queue, the audit log, the
    hooks and settings files, ``.env``, the MCP sign-in store): they resolve a path with the host's
    rules, where ``/mnt/c/Users/x/.chimera/audit.jsonl`` is a folder that does not exist and passes.
    The command still runs as written; this is only a second reading of it.

    Each word is folded as the distro would (:func:`_posix_abs`), so ``/mnt//c``, ``//mnt/c``,
    ``/usr/../mnt/c`` and ``../../../mnt/c/x`` from ``cwd`` are all read as the drive they are, and
    ``/mnt/c`` alone is the drive's ROOT (``C:/``, never the drive-relative ``C:``). With ``follow_cd``
    a ``cd``/``pushd`` moves the folder later relative words are read against, whatever its target looks
    like (``cd / && cat mnt/c/x``, ``cd /mnt; cd c; cat Users/x``), and ``-C DIR`` / ``--directory DIR``
    / ``--cd DIR`` do the same for the rest of their command. Callers read it several ways (with and
    without following, and with absolute words only): a ``cd`` inside a subshell does not carry over,
    and a ``-C`` that is not a folder (``grep -C 3``) must not hide the plain reading. Only adds
    readings: a word that is not on a drive is left exactly as written.

    Known limits, said once: what a shell computes is not read (``cd $(echo /mnt/c)``, variables,
    ``~`` in the distro, ``$(ls)``, symlinks, a path assembled at run time), and the fences' own
    limits stay as they are locally (``rm -rf`` of the data folder by name, ``find -delete`` that names
    nothing, ``git show`` of a tracked file).
    """
    parts = _WORD_SPLIT.split(text)
    cur = cwd  # moved by cd
    here = cwd  # the folder of THIS command: `cur`, or what -C entered
    expect_target = False
    expect_dir = False
    for index in range(0, len(parts), 2):
        word = parts[index]
        if index and _ENDS_COMMAND.search(parts[index - 1]):
            expect_target = expect_dir = False
            here = cur
        if not word:
            continue
        if word.lower() in ("cd", "pushd"):
            expect_target = True
            continue
        if word in _ENTERS_FOLDER:
            expect_dir = True
            continue
        glued = word.startswith("-C") and len(word) > 2 and not word.startswith("--")
        if glued:  # `tar -C/mnt/c`
            prefix, word = "-C", word[2:]
        else:
            prefix = ""
        entering = expect_target or expect_dir or glued
        if entering and word.startswith("-") and not glued:
            continue  # `cd -P dir`: the option is not the target
        absolute = _posix_abs(word, here, directory=entering)
        if expect_target:
            expect_target = False
            if follow_cd and absolute is not None:
                cur = here = absolute
        elif expect_dir or glued:
            expect_dir = False
            if follow_cd and absolute is not None:
                here = absolute
        mapped = _on_drive(absolute) if absolute is not None else None
        if mapped is not None:
            parts[index] = prefix + mapped
    return "".join(parts)


def wsl_argv(command: str, linux_cwd: str, distro: str = "") -> list[str]:
    """The argument vector for one command. The command is a single element: no Windows shell sees it."""
    argv = [WSL_EXE]
    if distro:
        if not valid_distro(distro):
            raise WslPathError("the distro name is not valid")
        argv += ["-d", distro]
    return [*argv, "--cd", linux_cwd, "--exec", "bash", "-lc", command]


class WslSandbox:
    """Runs a command in a WSL distro, in the workspace's folder as the distro sees it.

    Deliberately **not** a :class:`~chimera.sandbox.local.LocalSandbox`: every place that treats that
    class as "a process on this host I can adopt, wrap or kill as a job" would be wrong about this
    one, whose process lives in the distro. Background jobs are therefore not offered here.
    """

    def __init__(self, workspace: Path | str, distro: str = "") -> None:
        if distro and not valid_distro(distro):
            raise WslPathError("the distro name is not valid")
        self.workspace = Path(workspace)
        self.distro = distro

    @staticmethod
    def is_isolated() -> bool:
        """Never: WSL shares the disk, the user and the network with Windows."""
        return False

    def run(self, command: str, *, timeout: int = 60, cwd: Path | None = None) -> SandboxResult:
        from chimera.sandbox.local import LocalSandbox

        where = cwd if cwd is not None else self.workspace
        try:
            argv = wsl_argv(command, to_wsl_path(where, self.workspace), self.distro)
        except WslPathError as exc:
            return SandboxResult(exit_code=1, stderr=f"error: not run: {exc}")
        # `cwd` is the Windows folder `wsl.exe` itself starts in; the distro's is the `--cd` above.
        return LocalSandbox()._run_process(
            argv, timeout=timeout, cwd=where, use_shell=False, on_timeout=None
        )


def parse_distro_list(raw: bytes) -> list[str]:
    """The names in ``wsl.exe -l -q``'s output.

    That output is UTF-16LE, with a byte-order mark on some builds and none on others, and read as
    anything else it comes back with a NUL between every letter — a distro called ``Ubuntu`` that
    matches no name. Lines that are not a valid name are dropped rather than offered.
    """
    text = raw.decode("utf-16-le", errors="ignore") if b"\x00" in raw else raw.decode("utf-8", "ignore")
    names: list[str] = []
    for line in text.replace("\ufeff", "").splitlines():
        name = line.strip().replace("\x00", "")
        # `-l -q` marks the default with "(Default)" only without -q; kept anyway for old builds.
        name = re.sub(r"\s*\(Default\)\s*$", "", name)
        if valid_distro(name) and name not in names:
            names.append(name)
    return names


def list_wsl_distros() -> list[str]:
    """The installed distros, or an empty list when WSL is absent, broken or slow to answer."""
    if not wsl_supported():
        return []
    try:
        done = subprocess.run(  # noqa: S603 - fixed argv, no shell
            [WSL_EXE, "-l", "-q"],
            capture_output=True,
            timeout=_LIST_TIMEOUT,
            check=False,
            stdin=subprocess.DEVNULL,
            **no_window(),
        )
    except (OSError, subprocess.SubprocessError):
        return []
    return parse_distro_list(done.stdout or b"") if done.returncode == 0 else []
