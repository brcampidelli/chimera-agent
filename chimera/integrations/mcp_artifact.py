"""What a stdio MCP server actually RUNS, as opposed to what it LISTS (P1-12, audit a06-04).

:mod:`chimera.integrations.mcp_pins` pins the tool listing. A listing is what the server says about
itself; it says nothing about which code answered. ``npx some-server@1.2.3`` and
``npx some-server@1.2.4`` can list the same tools, and so can a script edited in place, so a pin of the
listing alone let the executable change underneath an approval. This module computes the second half
of the pin: the **artifact identity**.

**It is computed from the EFFECTIVE launch**, the one ``mcp_config.effective_launch`` hands to the
process: ``{env:NAME}`` references already expanded, ``cwd`` resolved. Describing the line as written
in ``mcp.json`` let ``{env:MCP_ENTRY}`` point at a different script with the pin unchanged. But what is
STORED and SHOWN is the line as written (the references, never their values): an expanded value can be
a token (``-e TOKEN={env:API_TOKEN}``), and the pin file, the diff, the API, the log and the UI must not
carry it. The expanded line enters the identity only as a SHA-256 (``args_sha256``).

**It is computed BEFORE the server starts** (``mcp_pool._fill``), and a changed identity holds the mount
without ever running the artifact: a check after the handshake reads whatever the server chose to leave
on disk by then. What remains is the window between the hash and the exec; closing it needs the
operating system to run the very bytes that were hashed, which no launcher here offers.

**What it contains** (a JSON-able dict; no secret is ever in clear):

``command`` / ``args`` / ``args_sha256`` / ``cwd``
    The launch line as configured, and the hash of the effective one.
``executable``
    The command resolved EXACTLY as the subprocess resolves it (``proc.stdio.launch_program``: a command
    with a separator is relative to the server's ``cwd``, a bare name is looked up on ``PATH``, the
    server's own ``PATH`` when its ``env`` sets one), through symlinks, and the SHA-256 of that file.
    No exemption by basename: a swapped ``node`` is a changed server, and so is a Node update.
``package``
    For a package launcher (``npx``/``bunx``/``pnpm``/``uvx``/``pipx``), the name and the version
    written in the spec.
``files``
    The SHA-256 of the ENTRY POINT's code and nothing else: the first script argument of an
    interpreter (``python``/``node``/``deno``/``bun``...), the module of ``python -m``, and the folder
    of either, recursively, symlinks followed to the real target, skipping ``.git``, ``node_modules``,
    ``__pycache__``, ``.venv`` and ``dist`` below the folder. Every other argument that is a path (a
    database, a data file, a working folder) enters by its PATH only, never its content: a SQLite file
    changes every time the server runs, and holding the server for that would hold it forever. The
    folder walk also skips the Chimera home (the pin file lives there), the pin/config files by name,
    and any file or folder an argument names.
``env``
    For every variable that reaches the server (its ``env`` block, plus the load-effect ones from the
    process environment: ``NODE_OPTIONS``, ``PYTHONPATH``, ``LD_PRELOAD``, ``DYLD_*``, ``npm_config_*``
    and the like), the name and the SHA-256 of the value. Values are never shown or logged.
``unverifiable``
    The reasons something could not be established. NOT a hold: the owner is told (the held diff and
    the MCP screen), and the identity still changes when what could be read changes.

**Reading files is bounded and safe.** Only REGULAR files are read (``S_ISREG`` after following the
link): a FIFO, a socket, a device or ``/dev/zero`` behind a symlink is skipped, never opened for
reading — an open on a FIFO without a writer blocks forever, and this runs before the connect timeout
of every server behind it. Bytes counted against the budget are the bytes actually READ, so a file that
grows while it is read stops at the ceiling. Before any content is read the declared sizes are summed
and a folder over the ceiling is declared unverifiable without reading a byte. Hashes are cached in
the process by (path, ``st_mtime_ns``, ``st_size``, ``st_ino``), so a boot or a retry re-reads only what
moved.

**What cannot be verified offline, said plainly.** ``npx pkg``, ``pkg@latest``, ``uvx pkg`` and
``pipx run pkg`` resolve at launch to whatever the registry serves that day, and nothing local says
which version that was (reading the manager's cache is manager- and version-specific, and was left out
rather than guessed). They are marked unverifiable; writing an exact version in ``mcp.json``
(``pkg@1.2.3``, ``pkg==1.2.3``) makes a change of version a change of the launch line, which is held.
A module that is not on ``cwd``/``PYTHONPATH`` (an installed package), a folder over the limits and an
executable that cannot be found are unverifiable for the same reason.

Remote (URL) servers have no artifact on this machine: :func:`artifact_identity` returns ``None``.
"""

from __future__ import annotations

import hashlib
import os
import re
import stat
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from chimera.proc.stdio import launch_program

#: Limits on what one identity may read. Past them the identity says ``unverifiable``, never ``ok``.
MAX_FILES = 5000
MAX_BYTES = 512 * 1024 * 1024

_CHUNK = 1024 * 1024
_SKIP_DIRS = frozenset({".git", "node_modules", "__pycache__", ".venv", "dist"})
#: Files the Chimera home keeps, whatever folder a script happens to share with it.
_OWN_FILES = frozenset({"mcp.json", "mcp_pins.json", "mcp_tests.json"})
#: ...with their `.lock` sibling and the `<name>.<random>.tmp` files `atomic_write_text` leaves mid-write.
_OWN_PREFIXES = tuple(sorted(_OWN_FILES))

#: Variables that change what code a process LOADS. Matched by exact name, or by prefix below.
_LOAD_EFFECT = frozenset({
    "NODE_OPTIONS", "NODE_PATH", "PYTHONPATH", "PYTHONSTARTUP", "PYTHONHOME", "LD_PRELOAD",
    "RUBYOPT", "PERL5OPT", "JAVA_TOOL_OPTIONS",
})
_LOAD_EFFECT_PREFIXES = ("DYLD_", "NPM_CONFIG_")

_NPX_LIKE = frozenset({"npx", "bunx", "pnpm", "npm", "yarn"})
_UV_LIKE = frozenset({"uvx", "uv", "pipx"})
_PYTHON = frozenset({"python", "python3", "py"})
#: Launchers whose script is the program (read by the interpreter's own option rules below). Everything else (``uvx``, ``docker``, a bare
#: binary) has its code in the executable or the package, not in an argument.
_INTERPRETERS = _PYTHON | frozenset({"node", "deno", "bun", "ruby", "perl", "php", "lua"})
#: A file with one of these suffixes among the arguments may be code something loads.
_CODE_SUFFIXES = (".py", ".js", ".mjs", ".cjs", ".ts", ".mts", ".cts", ".jsx", ".tsx", ".rb", ".pl", ".php", ".lua")
_SKIP_WORDS = frozenset({"dlx", "exec", "run", "tool", "x"})
_EXACT_VERSION = re.compile(r"^v?\d+(?:\.\d+){0,3}(?:[-+][0-9A-Za-z.+-]*)?$")

#: Whether a digest may be reused BETWEEN identity computations (a retry, the next connect). Only where
#: ``st_ctime_ns`` is the inode CHANGE time, which a writer cannot set: POSIX moves it on every write and
#: on every ``utime``, so rewriting a file in place with the same size and a restored mtime still changes
#: the key. On Windows ``st_ctime`` is the CREATION time, an in-place rewrite leaves it alone, and the
#: cache would hand the old digest to new bytes: there a digest lives only inside one computation.
_PERSISTENT_CACHE = os.name != "nt"
#: (real path, mtime_ns, size, inode, ctime_ns) -> SHA-256. In-process; see the module docstring.
_HASH_CACHE: dict[tuple[str, int, int, int, int], str] = {}
_HASH_CACHE_MAX = 50_000


class _Budget:
    def __init__(self) -> None:
        self.declared = 0  # st_size of the regular files admitted
        self.files = 0
        self.read = 0  # bytes actually read
        self.over = False
        #: Digests of this one computation (the same file reached twice is read once).
        self.local: dict[tuple[str, int, int, int, int], str] = {}

    def admit(self, sizes: Iterable[int]) -> bool:
        """Count files by their DECLARED size, before any is read. False (and ``over``) past a limit."""
        for size in sizes:
            self.files += 1
            self.declared += size
        if self.files > MAX_FILES or self.declared > MAX_BYTES:
            self.over = True
        return not self.over


def _launcher_name(command: str) -> str:
    base = Path(command.replace("\\", "/")).name.lower()
    for suffix in (".exe", ".cmd", ".bat", ".ps1"):
        if base.endswith(suffix):
            return base[: -len(suffix)]
    return base


def _split_version(spec: str) -> tuple[str, str]:
    """``name`` and version of a package spec: ``@s/n@1.2.3``, ``n@1.2.3``, ``n==1.2.3``, ``n``."""
    if "==" in spec:
        name, _, version = spec.partition("==")
        return name, version or "unpinned"
    at = spec.rfind("@")
    if at > 0:  # a leading "@" is the scope of ``@scope/name``, not a version separator
        return spec[:at], spec[at + 1 :] or "unpinned"
    return spec, "unpinned"


def _package_of(launcher: str, args: list[str]) -> dict[str, str] | None:
    if launcher not in _NPX_LIKE | _UV_LIKE:
        return None
    rest = list(args)
    spec: str | None = None
    while rest:
        word = rest.pop(0)
        if word == "--from" and rest:  # uvx --from pkg==1.2 tool
            spec = rest.pop(0)
            break
        if word.startswith("-") or word in _SKIP_WORDS:
            continue
        spec = word
        break
    if not spec:
        return None
    name, version = _split_version(spec)
    return {"manager": launcher, "name": name, "version": version}


def _regular_stat(path: str) -> os.stat_result | None:
    """``os.stat`` of ``path`` FOLLOWING links, only if it is a regular file; else None."""
    try:
        st = os.stat(path)
    except OSError:
        return None
    return st if stat.S_ISREG(st.st_mode) else None


def _read_digest(path: str, budget: _Budget) -> str | None:
    """SHA-256 of ``path`` read in a stream, counting the bytes READ against the budget.

    Opened non-blocking and re-checked with ``fstat``: a path that became a FIFO between the ``stat`` and
    here is refused instead of blocking, and the read stops at the ceiling whatever ``st_size`` said.
    """
    flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0)
    try:
        fd = os.open(path, flags)
    except OSError:
        return None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            os.close(fd)
            return None
        digest = hashlib.sha256()
        with os.fdopen(fd, "rb") as handle:  # closes fd
            while chunk := handle.read(_CHUNK):
                budget.read += len(chunk)
                if budget.read > MAX_BYTES:
                    budget.over = True
                    return None
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return None


def _file_digest(path: str, st: os.stat_result, budget: _Budget) -> str | None:
    key = (path, st.st_mtime_ns, st.st_size, st.st_ino, st.st_ctime_ns)
    cached = budget.local.get(key) or (_HASH_CACHE.get(key) if _PERSISTENT_CACHE else None)
    if cached is not None:
        return cached
    digest = _read_digest(path, budget)
    if digest is None:
        return None
    try:  # a file that moved while it was read is not cached: the digest may describe neither version
        after = os.stat(path)
        stable = (after.st_mtime_ns, after.st_size, after.st_ctime_ns) == (
            st.st_mtime_ns, st.st_size, st.st_ctime_ns)
    except OSError:
        stable = False
    if stable:
        budget.local[key] = digest
        if _PERSISTENT_CACHE:
            if len(_HASH_CACHE) >= _HASH_CACHE_MAX:
                _HASH_CACHE.clear()
            _HASH_CACHE[key] = digest
    return digest


def _hash_one(path: Path, budget: _Budget) -> str | None:
    """SHA-256 of one regular file (links followed), or None if it is not one / over the budget."""
    real = os.path.realpath(path)
    st = _regular_stat(real)
    if st is None or not budget.admit([st.st_size]):
        return None
    return _file_digest(real, st, budget)


def _hash_tree(root: Path, budget: _Budget, skip: frozenset[str]) -> dict[str, str] | None:
    """Every regular file under ``root`` (relative posix path -> SHA-256), or None if over the limits.

    Two passes: list the regular files and sum their declared sizes (nothing is read, so a folder far
    over the ceiling costs a ``stat`` per file, not 512 MiB of I/O), then read. Symlinks are followed to
    their real target, with the real directories seen so far remembered so a link back up the tree
    cannot loop; anything that is not a regular file is left out. ``skip`` holds real paths (files or
    folders) the walk never enters.
    """
    found: list[tuple[str, str, os.stat_result]] = []
    seen: set[str] = set()
    for current, dirs, names in os.walk(root, followlinks=True):
        real_dir = os.path.realpath(current)
        if real_dir in seen:
            dirs[:] = []
            continue
        seen.add(real_dir)
        dirs[:] = sorted(
            d for d in dirs
            if d not in _SKIP_DIRS and os.path.realpath(os.path.join(current, d)) not in skip
        )
        for name in sorted(names):
            if name.startswith(_OWN_PREFIXES):
                continue
            path = os.path.join(current, name)
            real = os.path.realpath(path)
            if real in skip:
                continue
            st = _regular_stat(real)
            if st is not None:
                found.append((os.path.relpath(path, root).replace(os.sep, "/"), real, st))
    if not budget.admit(st.st_size for _, _, st in found):
        return None
    out: dict[str, str] = {}
    for rel, real, st in found:
        digest = _file_digest(real, st, budget)
        if budget.over:
            return None
        if digest is not None:
            out[rel] = digest
    return out


def _is_load_effect(name: str) -> bool:
    upper = name.upper()
    return upper in _LOAD_EFFECT or upper.startswith(_LOAD_EFFECT_PREFIXES)


def _env_digests(env: Mapping[str, str], environ: Mapping[str, str]) -> dict[str, str]:
    """name -> SHA-256 of the value, for what reaches the server: its ``env`` block over the
    load-effect variables of the process. Values are hashed here and never kept."""
    effective = {k: v for k, v in environ.items() if _is_load_effect(k)}
    effective.update(env)
    return {
        name: hashlib.sha256(str(value).encode("utf-8")).hexdigest()
        for name, value in sorted(effective.items())
    }


def _search_dirs(cwd: Path, effective_env: Mapping[str, str]) -> list[Path]:
    dirs = [cwd]
    for entry in (effective_env.get("PYTHONPATH") or "").split(os.pathsep):
        if entry:
            dirs.append(_resolve_path(entry, cwd))
    return dirs


def _resolve_path(arg: str, cwd: Path) -> Path:
    candidate = Path(arg).expanduser()
    return candidate if candidate.is_absolute() else cwd / candidate


def _module_root(module: str, search: list[Path]) -> Path | None:
    """The folder that holds ``module`` (``a.b`` -> ``a/b`` package or ``a/b.py``), resolved by PATH
    from the search directories. ``importlib`` is deliberately not used: finding a spec can run code."""
    parts = module.split(".")
    for base in search:
        package = base.joinpath(*parts)
        if package.is_dir():
            return package
        file = base.joinpath(*parts[:-1], parts[-1] + ".py")
        if file.is_file():
            return file.parent
    return None


#: Per interpreter: options whose value is CODE the interpreter loads (hashed by content), options whose
#: value is a literal program (``-e``/``-c``: already part of the arguments, which are compared), options
#: that take a separate value that is neither, and subcommands that come before the script.
_OPTIONS: dict[str, tuple[frozenset[str], frozenset[str], frozenset[str], frozenset[str]]] = {
    "node": (frozenset({"-r", "--require", "--import", "--loader", "--experimental-loader"}),
             frozenset({"-e", "--eval", "-p", "--print"}),
             frozenset({"--input-type", "-C", "--conditions", "--title", "--stack-trace-limit",
                        "--max-old-space-size", "--env-file"}),
             frozenset()),
    "bun": (frozenset({"-r", "--require", "--import", "--preload"}),
            frozenset({"-e", "--eval", "-p", "--print"}),
            frozenset({"--cwd", "--config", "-c", "--env-file"}),
            frozenset({"run", "x"})),
    "deno": (frozenset({"--preload", "--require", "--import"}),
             frozenset({"-e", "--eval"}),
             frozenset({"--config", "-c", "--import-map", "--lock", "--cert"}),
             frozenset({"run", "serve", "task"})),
    "python": (frozenset(), frozenset({"-c"}), frozenset({"-X", "-W", "-Q", "--check-hash-based-pycs"}),
               frozenset()),
    "ruby": (frozenset({"-r"}), frozenset({"-e"}), frozenset({"-I", "-C"}), frozenset()),
    "perl": (frozenset(), frozenset({"-e", "-E"}), frozenset({"-I", "-M", "-m"}), frozenset()),
    "php": (frozenset(), frozenset({"-r"}), frozenset({"-c", "-d"}), frozenset()),
    "lua": (frozenset(), frozenset({"-e"}), frozenset({"-l"}), frozenset()),
}


def _options_for(launcher: str) -> tuple[frozenset[str], frozenset[str], frozenset[str], frozenset[str]]:
    key = "python" if launcher in _PYTHON else launcher
    return _OPTIONS.get(key, (frozenset(), frozenset(), frozenset(), frozenset()))


def _interpreter_program(launcher: str, argv: list[str], base: Path) -> tuple[Path | None, list[Path]]:
    """The script the interpreter runs and the code files it loads, by the interpreter's own rule: the
    main script is the FIRST POSITIONAL argument that is not the value of an option (so
    ``node --require preload.js server.js`` runs ``server.js``, not ``preload.js``).

    A literal (``-e``/``-c``/``-p``/``--eval``) is the program itself, already part of the arguments the
    identity compares, and it does NOT end the analysis: ``node -e 0 --require server.cjs`` still loads
    ``server.cjs``, so every option after it is read. The rule is never to stop early in a way that
    leaves loaded code out of the hash: whatever the parse did not account for, any remaining argument
    that names an existing file with a code suffix is hashed by content as a precaution (a form this
    table does not know is then covered by being conservative, not by being right).
    """
    code_opts, eval_opts, value_opts, subcommands = _options_for(launcher)
    loaded: list[Path] = []
    script: Path | None = None
    literal = False
    i = 0
    while i < len(argv):
        arg = argv[i]
        i += 1
        if arg.startswith("-") and arg != "-":
            name, eq, value = arg.partition("=") if arg.startswith("--") else (arg, "", "")
            if name in code_opts:
                target = value if eq else (argv[i] if i < len(argv) else "")
                i += 0 if eq else 1
                if target and (path := _resolve_path(target, base)).is_file():
                    loaded.append(path)
            elif name in eval_opts:
                literal = True
                i += 0 if eq else 1
            elif name in value_opts and not eq:
                i += 1
            continue
        if literal or script is not None:
            continue  # an argument of the literal or of the script, not a program
        if arg in subcommands:
            continue
        candidate = _resolve_path(arg, base)
        if candidate.is_file():
            script = candidate
        break
    known = {os.path.realpath(p) for p in loaded} | ({os.path.realpath(script)} if script else set())
    for arg in argv:
        if arg.startswith("-") or not arg.lower().endswith(_CODE_SUFFIXES):
            continue
        candidate = _resolve_path(arg, base)
        if candidate.is_file() and os.path.realpath(candidate) not in known:
            known.add(os.path.realpath(candidate))
            loaded.append(candidate)
    return script, loaded


def artifact_identity(
    command: str,
    args: list[str],
    *,
    raw_args: list[str] | None = None,
    cwd: str | Path | None = None,
    url: str | None = None,
    env: Mapping[str, str] | None = None,
    environ: Mapping[str, str] | None = None,
    exclude: Iterable[str | Path] = (),
) -> dict[str, Any] | None:
    """The identity of what ``command args`` runs, or ``None`` for a remote (URL) server.

    ``args`` is the EFFECTIVE argument list (``{env:...}`` expanded) and decides what is hashed;
    ``raw_args`` is the list as configured and is the only form that is stored or shown. Pass the
    effective launch (``mcp_config.effective_launch``) and the ``env`` block; ``environ`` is the
    process environment (default ``os.environ``). ``exclude`` are paths the folder hash must never
    enter (the Chimera home). Reads files and starts nothing.
    """
    if url or not command:
        return None
    process_env = os.environ if environ is None else environ
    server_env = dict(env or {})
    base = Path(cwd) if cwd else Path.cwd()
    launcher = _launcher_name(command)
    argv = [str(a) for a in args]
    shown = [str(a) for a in (raw_args if raw_args is not None else args)]
    reasons: list[str] = []
    budget = _Budget()
    identity: dict[str, Any] = {
        "command": command,
        "args": shown,
        "args_sha256": hashlib.sha256("\0".join(argv).encode("utf-8")).hexdigest(),
        "cwd": str(base),
    }

    path_value = server_env.get("PATH") or process_env.get("PATH")
    located = launch_program(command, cwd=cwd, path=path_value)
    if located is None:
        reasons.append(f"executable {command!r} was not found")
    else:
        digest = _hash_one(Path(located), budget)
        if digest is None:
            reasons.append(f"executable {command!r} is not a readable regular file or is over the limits")
        else:
            identity["executable"] = {"path": os.path.realpath(located), "sha256": digest}

    # Paths the folder hash must not enter: the Chimera home, and every file or folder an argument
    # names (a database, a data file). They are in the identity by path, as part of the arguments.
    arg_paths = {
        os.path.realpath(p) for a in argv if not a.startswith("-") if (p := _resolve_path(a, base)).exists()
    }

    package = _package_of(launcher, shown)
    if package is not None:
        identity["package"] = package
        spec_dir = _resolve_path(package["name"], base)
        if spec_dir.is_dir():  # `uvx --from ./proj tool`: a local project is files, not a version
            skip = frozenset({os.path.realpath(e) for e in exclude}) | frozenset(
                p for p in arg_paths if p != os.path.realpath(spec_dir)
            )
            tree = _hash_tree(Path(os.path.realpath(spec_dir)), budget, skip)
            if tree is None:
                reasons.append(f"package folder {package['name']!r} is over the hashing limits")
            else:
                identity["files"] = {f"{package['name']}::{k}": v for k, v in tree.items()}
        elif "://" in package["name"] or package["name"].startswith("git+"):
            reasons.append(f"package {package['name']!r} is fetched from a URL: not verifiable offline")
        elif not _EXACT_VERSION.match(package["version"]):
            reasons.append(
                f"package {package['name']!r} has no exact version ({package['version']}): the version "
                "that runs cannot be verified offline"
            )

    files: dict[str, str] = dict(identity.get("files", {}))
    entries: list[tuple[str, Path, Path | None]] = []  # label, folder, the script itself
    code_files: set[str] = set()
    if launcher in _PYTHON and "-m" in argv and argv.index("-m") + 1 < len(argv):
        module = argv[argv.index("-m") + 1]
        found = _module_root(module, _search_dirs(base, {**process_env, **server_env}))
        if found is None:
            reasons.append(f"module {module!r} is not on cwd or PYTHONPATH (an installed package?)")
        else:
            entries.append((f"-m {module}", found, None))
    elif launcher in _INTERPRETERS:
        script, loaded = _interpreter_program(launcher, argv, base)
        if script is not None:
            real_script = Path(os.path.realpath(script))
            entries.append((script.name, real_script.parent, real_script))
        for code in loaded:  # a preload/loader/require runs before the program: its CONTENT counts
            real_code = os.path.realpath(code)
            code_files.add(real_code)
            digest = _hash_one(Path(real_code), budget)
            if digest is None:
                reasons.append(f"loaded code {code.name!r} is not a readable regular file or is over the limits")
            else:
                files[f"{code.name} (loaded)::{real_code}"] = digest
    excluded = frozenset({os.path.realpath(e) for e in exclude})
    done: set[str] = set()
    for label, root, script_file in entries:
        key = os.path.realpath(root)
        if key in done:
            continue
        done.add(key)
        script_real = os.path.realpath(script_file) if script_file else None
        # Other arguments that are paths stay out, unless they are, or hold, the entry point itself.
        skip = excluded | frozenset(
            p for p in arg_paths
            if p != script_real and p not in code_files and not key.startswith(p + os.sep) and p != key
        )
        tree = _hash_tree(Path(key), budget, skip)
        if tree is None:
            reasons.append(f"folder of {label!r} is over the hashing limits ({MAX_FILES} files, "
                           f"{MAX_BYTES // (1024 * 1024)} MiB)")
            continue
        files.update({f"{label}::{rel}": digest for rel, digest in tree.items()})
    if files:
        identity["files"] = dict(sorted(files.items()))

    envs = _env_digests(server_env, process_env)
    if envs:
        identity["env"] = envs
    if reasons:
        identity["unverifiable"] = sorted(set(reasons))
    return identity


def changed_parts(old: dict[str, Any], new: dict[str, Any]) -> list[str]:
    """Which parts of the identity moved, by NAME. Env values and file contents never appear."""
    parts: list[str] = []
    for key in ("command", "args", "cwd", "package"):
        if old.get(key) != new.get(key):
            parts.append(key)
    if old.get("args_sha256") != new.get("args_sha256") and old.get("args") == new.get("args"):
        parts.append("args (an expanded value)")
    if old.get("executable") != new.get("executable"):
        parts.append("executable")
    for key in ("files", "env"):
        before, after = old.get(key) or {}, new.get(key) or {}
        moved = sorted(n for n in set(before) | set(after) if before.get(n) != after.get(n))
        if moved:
            shown = ", ".join(moved[:8]) + (f" (+{len(moved) - 8} more)" if len(moved) > 8 else "")
            parts.append(f"{key}: {shown}")
    if old.get("unverifiable") != new.get("unverifiable"):
        parts.append("verifiability")
    return parts


def describe(identity: dict[str, Any] | None) -> str:
    """One line for the owner: what the server runs. The arguments are as CONFIGURED (``{env:NAME}``,
    never its value), and no env value and no secret is in it."""
    if not identity:
        return "(no local artifact)"
    line = " ".join([str(identity.get("command", "")), *map(str, identity.get("args", []))]).strip()
    parts = [line]
    executable = identity.get("executable")
    if isinstance(executable, dict):
        parts.append(f"executable {executable.get('path')} sha256:{str(executable.get('sha256'))[:12]}")
    package = identity.get("package")
    if isinstance(package, dict):
        parts.append(f"package {package.get('name')} @ {package.get('version')}")
    files = identity.get("files")
    if isinstance(files, dict) and files:
        parts.append(f"{len(files)} file(s) hashed")
    env = identity.get("env")
    if isinstance(env, dict) and env:
        parts.append("env: " + ", ".join(sorted(env)))
    unverifiable = identity.get("unverifiable")
    if isinstance(unverifiable, list) and unverifiable:
        parts.append("NOT VERIFIABLE: " + "; ".join(map(str, unverifiable)))
    return " | ".join(parts)


def unverifiable_reasons(identity: dict[str, Any] | None) -> list[str]:
    if not identity:
        return []
    reasons = identity.get("unverifiable")
    return [str(r) for r in reasons] if isinstance(reasons, list) else []
