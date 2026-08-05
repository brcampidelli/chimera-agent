"""Repo-map — a compact structural digest of the workspace for the agent's context.

The agent could grep and read files, but on a large repo it wastes turns just finding where a
symbol lives. A repo-map front-loads that: one line per file with its top-level definitions,
extracted with the stdlib ``ast`` (Python) or a small regex (TypeScript) — no dependency, no model
call. Dropped into the solve context, it lets the agent jump straight to the right file.

**What changed, and why it mattered.** The first version sorted the lines alphabetically and then
truncated at a character budget. Measured on this repository that meant 48 lines survived, 473 files
were dropped, and the file that made the cut was ``apps/desktop/src-tauri/build_sidecar.py`` — while
``chimera/core/agent.py``, ``chimera/core/autonomous.py`` and ``chimera/fusion/engine.py`` did not.
An agent handed that map was worse off than one handed nothing, because it looked like a map.

The defect was never extraction; it was **ordering**. So the map now builds the import graph it was
already parsing for free, ranks files by personalised PageRank over it, and spends the budget in
rank order. A file that half the codebase imports outranks one nothing imports, and files the task
names outrank both.

Deliberately cheap and bounded: it skips build/venv/cache noise, honours a light ``.gitignore``,
lists only top-level symbols (not every method), truncates to a character budget, and caches parse
results by (mtime, size) so an unchanged file is never re-parsed. A map is a table of contents.

Provenance: ranking a repo map by PageRank over a reference graph is aider's published technique
(2023). This is an independent implementation from that public description — no code was consulted.
"""

from __future__ import annotations

import ast
import fnmatch
import hashlib
import json
import os
import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from math import log
from pathlib import Path
from typing import Any

from chimera.telemetry import get_logger

_log = get_logger("core.repomap")

_DEFAULT_IGNORE = {
    ".git", "__pycache__", ".venv", "venv", "node_modules", ".mypy_cache",
    ".pytest_cache", ".ruff_cache", "dist", "build", ".chimera", ".idea", ".vscode",
}

#: Extensions the map understands. Anything else is invisible to it — stated here rather than
#: discovered by a reader wondering why their Go package never appears.
_PY = (".py",)
_TS = (".ts", ".tsx", ".js", ".jsx", ".mjs")

#: Longest single line in the digest. One file with two hundred exports must not spend the whole
#: budget; past this cap a line keeps its most *distinctive* symbols (see the IDF weighting below)
#: and says how many it dropped.
_MAX_LINE_CHARS = 240

#: PageRank parameters. Twenty iterations is far past convergence for a graph of a few thousand
#: nodes, and cheap enough (pure Python, no NumPy) that measuring the exact stopping point would
#: cost more than the iterations it saved.
_DAMPING = 0.85
_ITERATIONS = 20

#: How much of the score comes from *undirected* centrality rather than pure dependency.
#:
#: Pure PageRank over "A imports B" answers one question well — what does this repository depend on
#: — and answers the agent's other question badly. Measured here, it put ``chimera/telemetry.py``
#: first and ``chimera/api/app.py``, the single largest surface in the tree, at position 127; the
#: orchestrators rank last precisely BECAUSE nothing imports them. An entry point is the last thing
#: a map should hide. Blending in the symmetrised graph fixes that (with 0.3, ``api/app.py`` moves
#: 127 → 18 and ``core/autonomous.py`` 45 → 15) without letting it take over: at 1.0 the leaf-ward
#: signal collapses and ``fusion/engine.py`` falls 6 → 64. 0.3 was chosen by measuring 0.0/0.3/0.5/
#: 0.7/1.0 on this repository, not by taste — rerun that sweep before changing it.
_ORCHESTRATOR_WEIGHT = 0.3


# --------------------------------------------------------------------------------------------
# Scanning
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class _FileInfo:
    """One file's contribution to the map: what it defines, and what it reaches for."""

    rel: str
    symbols: tuple[str, ...]
    #: Raw import specifiers, unresolved. Dotted module names for Python; relative paths for
    #: TypeScript. Resolution needs the whole file set, so it happens once, later.
    imports: tuple[str, ...]


def _load_gitignore(root: Path) -> list[str]:
    """Read simple glob patterns from a top-level .gitignore (comments/blank lines skipped)."""
    path = root / ".gitignore"
    if not path.is_file():
        return []
    patterns: list[str] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            patterns.append(stripped.rstrip("/"))
    return patterns


def _is_ignored(name: str, rel_posix: str, patterns: list[str]) -> bool:
    if name in _DEFAULT_IGNORE:
        return True
    return any(
        fnmatch.fnmatch(name, pat) or fnmatch.fnmatch(rel_posix, pat) or fnmatch.fnmatch(rel_posix, f"*/{pat}")
        for pat in patterns
    )


def _package_of(rel: str) -> str:
    """The dotted package a Python file lives in (``chimera/core/agent.py`` → ``chimera.core``)."""
    head, _, _ = rel.rpartition("/")
    return head.replace("/", ".")


def _module_name(rel: str) -> str:
    """The dotted module a Python file provides (a package's ``__init__`` provides the package)."""
    stem = rel[: -len(".py")]
    if stem.endswith("/__init__"):
        stem = stem[: -len("/__init__")]
    elif stem == "__init__":
        return ""
    return stem.replace("/", ".")


def _scan_python(text: str, rel: str) -> tuple[list[str], list[str]]:
    """Top-level definitions and imported module names. Empty lists on a parse error."""
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return [], []
    symbols: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            symbols.append(node.name)
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            symbols.append(f"{node.name}()")

    package = _package_of(rel)
    imports: list[str] = []
    for stmt in ast.walk(tree):
        if isinstance(stmt, ast.Import):
            imports.extend(alias.name for alias in stmt.names)
        elif isinstance(stmt, ast.ImportFrom):
            node = stmt
            base = node.module or ""
            if node.level:  # relative: resolve against this file's own package
                parts = package.split(".") if package else []
                climb = node.level - 1  # level 1 is the current package, 2 is its parent, …
                if climb:
                    parts = parts[: max(0, len(parts) - climb)]
                base = ".".join([*parts, base]) if base else ".".join(parts)
            if not base:
                continue
            imports.append(base)
            # `from pkg import mod` — each name might itself be a submodule, and the ones that are
            # not simply fail to resolve later. Counting both is what gives an edge its weight: a
            # module imported for five symbols is reached for harder than one imported for one.
            imports.extend(f"{base}.{alias.name}" for alias in node.names if alias.name != "*")
    return symbols, imports


_TS_EXPORT = re.compile(
    r"^export\s+(?:default\s+)?(?:async\s+)?"
    r"(?:function|class|const|let|var|interface|type|enum)\s+([A-Za-z_$][\w$]*)",
    re.MULTILINE,
)
#: Only *relative* specifiers. A path alias (`@/lib/x`) needs the tsconfig to resolve and is
#: deliberately not guessed — a wrong edge is worse than a missing one, because it moves rank.
_TS_IMPORT = re.compile(r"""(?:from|import)\s*\(?\s*['"](\.[^'"]+)['"]""")


def _scan_typescript(text: str, _rel: str) -> tuple[list[str], list[str]]:
    """Exported names and relative import specifiers, by regex.

    Heuristic on purpose: a real parser means a native dependency and a grammar per language, for a
    gain that is small in a repository this shape. It catches the common `export function|class|
    const|type` forms and misses re-export barrels and destructured exports — stated so nobody
    mistakes the map for an index.
    """
    symbols = [m.group(1) for m in _TS_EXPORT.finditer(text)]
    return symbols, [m.group(1) for m in _TS_IMPORT.finditer(text)]


def _scan_file(path: Path, rel: str) -> _FileInfo:
    text = path.read_text(encoding="utf-8", errors="replace")
    if rel.endswith(_PY):
        symbols, imports = _scan_python(text, rel)
    else:
        symbols, imports = _scan_typescript(text, rel)
    return _FileInfo(rel, tuple(symbols), tuple(imports))


# --------------------------------------------------------------------------------------------
# Parse cache — keyed by (mtime, size), so an unchanged file is never re-parsed
# --------------------------------------------------------------------------------------------


def _cache_path(root: Path) -> Path | None:
    """Where this workspace's parse cache lives, or None if there is nowhere to put it.

    Best-effort throughout: a cache that cannot be read or written must degrade to "parse it again",
    never to an error. A stale entry is impossible by construction — the key is the file's own
    mtime and size, so a changed file simply misses.
    """
    try:
        from chimera.config import get_settings

        digest = hashlib.sha256(str(root).encode("utf-8")).hexdigest()[:16]
        return get_settings().home / "repomap" / f"{digest}.json"
    except Exception as exc:  # noqa: BLE001 — the cache is an optimisation, never a requirement
        _log.debug("repo-map cache unavailable: %s", exc)
        return None


def _load_cache(path: Path | None) -> dict[str, dict[str, Any]]:
    if path is None or not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception as exc:  # noqa: BLE001
        _log.debug("repo-map cache unreadable, reparsing: %s", exc)
        return {}


def _save_cache(path: Path | None, entries: dict[str, dict[str, Any]]) -> None:
    if path is None:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(entries), encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        _log.debug("repo-map cache not written: %s", exc)


def _collect(root: Path) -> list[_FileInfo]:
    """Walk the workspace and return one ``_FileInfo`` per mapped file, cache-assisted."""
    patterns = _load_gitignore(root)
    cache_path = _cache_path(root)
    cached = _load_cache(cache_path)
    fresh: dict[str, dict[str, Any]] = {}
    infos: list[_FileInfo] = []

    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = Path(dirpath).relative_to(root)
        # Prune ignored directories in place so os.walk never descends into them.
        dirnames[:] = [
            d for d in sorted(dirnames)
            if not _is_ignored(d, (rel_dir / d).as_posix(), patterns)
        ]
        for filename in sorted(filenames):
            if not filename.endswith(_PY + _TS):
                continue
            rel = (rel_dir / filename).as_posix().lstrip("./")
            if _is_ignored(filename, rel, patterns):
                continue
            path = Path(dirpath) / filename
            try:
                stat = path.stat()
            except OSError:
                continue
            key = f"{stat.st_mtime_ns}:{stat.st_size}"
            entry = cached.get(rel)
            if entry is not None and entry.get("key") == key:
                info = _FileInfo(
                    rel,
                    tuple(str(s) for s in entry.get("symbols", []) or []),
                    tuple(str(i) for i in entry.get("imports", []) or []),
                )
            else:
                try:
                    info = _scan_file(path, rel)
                except OSError:
                    continue
            infos.append(info)
            fresh[rel] = {"key": key, "symbols": list(info.symbols), "imports": list(info.imports)}

    if fresh != cached:
        _save_cache(cache_path, fresh)
    return infos


# --------------------------------------------------------------------------------------------
# Ranking
# --------------------------------------------------------------------------------------------


def _resolve_ts(spec: str, rel: str, known: set[str]) -> str | None:
    """Resolve a relative TypeScript specifier to a file in the map, or None."""
    base = (Path(rel).parent / spec).as_posix()
    base = os.path.normpath(base).replace("\\", "/")
    for candidate in (base, *(f"{base}{ext}" for ext in _TS), *(f"{base}/index{ext}" for ext in _TS)):
        if candidate in known:
            return candidate
    return None


def _edges(infos: list[_FileInfo]) -> dict[str, Counter[str]]:
    """The import graph: ``edges[importer][imported] = how many times it was reached for``.

    Direction matters and is easy to get backwards. Rank flows FROM the importer TO the imported,
    so a module half the codebase depends on accumulates rank — which is the thing an agent needs
    to see first.
    """
    known = {info.rel for info in infos}
    modules = {
        name: info.rel
        for info in infos
        if info.rel.endswith(_PY) and (name := _module_name(info.rel))
    }
    graph: dict[str, Counter[str]] = {}
    for info in infos:
        out: Counter[str] = Counter()
        for spec in info.imports:
            target = modules.get(spec) if info.rel.endswith(_PY) else _resolve_ts(spec, info.rel, known)
            if target and target != info.rel:  # a self-import carries no information
                out[target] += 1
        if out:
            graph[info.rel] = out
    return graph


def _symmetrised(graph: dict[str, Counter[str]]) -> dict[str, Counter[str]]:
    """The same graph with every edge readable in both directions.

    Ranked over this, "central" stops meaning "depended upon" and starts meaning "well connected",
    which is the only reading under which an entry point scores at all.
    """
    both: dict[str, Counter[str]] = {}
    for source, targets in graph.items():
        for target, weight in targets.items():
            both.setdefault(source, Counter())[target] += weight
            both.setdefault(target, Counter())[source] += weight
    return both


def _personalisation(rels: list[str], task: str, focus: Iterable[str]) -> dict[str, float]:
    """Weight the files the caller is plainly interested in, or nothing when there is no signal.

    A file counts as named when its stem appears in the task text — deliberately crude, because the
    alternative is guessing, and an empty personalisation vector degrades to plain PageRank rather
    than to a wrong one.
    """
    wanted = {f.replace("\\", "/").lstrip("./") for f in focus}
    lowered = task.lower()
    weights: dict[str, float] = {}
    for rel in rels:
        stem = Path(rel).stem
        if rel in wanted or (len(stem) > 3 and stem.lower() in lowered):
            weights[rel] = 1.0
    return weights


def _pagerank(rels: list[str], graph: dict[str, Counter[str]], personal: dict[str, float]) -> dict[str, float]:
    """Personalised PageRank by power iteration. Pure Python; no NumPy, no optional dependency."""
    n = len(rels)
    if n == 0:
        return {}
    total = sum(personal.values())
    seed = (
        {rel: personal.get(rel, 0.0) / total for rel in rels}
        if total > 0
        else dict.fromkeys(rels, 1.0 / n)
    )
    rank = dict(seed)
    index = set(rels)
    for _ in range(_ITERATIONS):
        nxt = {rel: (1.0 - _DAMPING) * seed[rel] for rel in rels}
        dangling = 0.0
        for rel in rels:
            out = graph.get(rel) or Counter()
            weight = sum(out.values())
            if not weight:
                # A file that imports nothing in-repo would otherwise leak its rank out of the
                # graph; redistributing it through the seed keeps the vector normalised.
                dangling += rank[rel]
                continue
            for target, count in out.items():
                if target in index:
                    nxt[target] += _DAMPING * rank[rel] * (count / weight)
        for rel in rels:
            nxt[rel] += _DAMPING * dangling * seed[rel]
        rank = nxt
    return rank


def _idf(infos: list[_FileInfo]) -> dict[str, float]:
    """How distinctive each symbol name is. ``run`` in three hundred files says less than ``run``
    in two, and when a line has to be trimmed it is the generic names that should go first."""
    n = max(1, len(infos))
    df: Counter[str] = Counter()
    for info in infos:
        df.update(set(info.symbols))
    return {name: log(n / (1 + count)) for name, count in df.items()}


def _line(info: _FileInfo, idf: dict[str, float]) -> str:
    """One file's line, trimmed to ``_MAX_LINE_CHARS`` by dropping its least distinctive symbols.

    Kept in source order after the trim: source order is information (a module's entry points tend
    to come first), and reordering it would only make the map harder to read for no gain.
    """
    if not info.symbols:
        return info.rel
    full = f"{info.rel}: {', '.join(info.symbols)}"
    if len(full) <= _MAX_LINE_CHARS:
        return full
    ordered = sorted(range(len(info.symbols)), key=lambda i: -idf.get(info.symbols[i], 0.0))
    kept: list[int] = []
    # The ", … (+N)" suffix is part of the line, so it has to be paid for before the symbols are —
    # budgeting it afterwards is how a cap ends up being exceeded by exactly the length of the note
    # that says the cap was applied.
    used = len(info.rel) + 2 + len(", … (+000)")
    for i in ordered:
        cost = len(info.symbols[i]) + 2
        if used + cost > _MAX_LINE_CHARS:
            break
        kept.append(i)
        used += cost
    shown = [info.symbols[i] for i in sorted(kept)]
    dropped = len(info.symbols) - len(shown)
    return f"{info.rel}: {', '.join(shown)}" + (f", … (+{dropped})" if dropped else "")


def build_repo_map(
    root: Path,
    *,
    max_chars: int = 4000,
    task: str = "",
    focus: Iterable[str] = (),
) -> str:
    """A one-line-per-file map of the workspace's symbols, ranked by importance and truncated.

    Files are ordered by personalised PageRank over the import graph, so the budget is spent on the
    files the repository actually depends on rather than on whichever names sort first. ``task`` and
    ``focus`` bias that ranking toward what the caller is working on; with neither, it is plain
    PageRank. Empty string when there is nothing to map.
    """
    root = root.resolve()
    infos = _collect(root)
    if not infos:
        return ""

    rels = [info.rel for info in infos]
    graph = _edges(infos)
    seed = _personalisation(rels, task, focus)
    depended = _pagerank(rels, graph, seed)
    connected = _pagerank(rels, _symmetrised(graph), seed)
    rank = {
        rel: (1.0 - _ORCHESTRATOR_WEIGHT) * depended.get(rel, 0.0)
        + _ORCHESTRATOR_WEIGHT * connected.get(rel, 0.0)
        for rel in rels
    }
    idf = _idf(infos)
    # Ties broken by path so the map is byte-stable across runs — a digest that reshuffles itself
    # between two identical runs would poison the prompt cache for no reason.
    ordered = sorted(infos, key=lambda i: (-rank.get(i.rel, 0.0), i.rel))

    out: list[str] = []
    used = 0
    omitted = 0
    for info in ordered:
        line = _line(info, idf)
        if used + len(line) + 1 > max_chars:
            omitted += 1
            continue
        out.append(line)
        used += len(line) + 1
    text = "\n".join(out)
    if omitted:
        text += f"\n... [{omitted} more file(s) omitted for space — least-referenced first]"
    return text
