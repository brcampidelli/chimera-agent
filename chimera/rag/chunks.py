"""Cutting a repository into pieces worth retrieving.

The unit of retrieval decides what retrieval can answer. Fixed-size windows — the default
everywhere — cut through the middle of functions, so a hit lands on half a body with no signature
and the model receives code it cannot attribute. Whole files go the other way: one 800-line module
is one embedding, and its vector is the average of everything it does, which is close to nothing.

So the unit here is the **symbol**: a function, a method, a class, with its decorators and its
docstring, spanning the lines the parser says it spans. That is also the unit a person names when
they ask, which is what makes a hit answerable — "it is in `resolve_verify`, lines 61-94" rather
than "somewhere in app.py".

Python is parsed with :mod:`ast` — no dependency, exact spans, and the same module `repomap`
already uses. Everything else falls back to overlapping line windows and SAYS so, because a
TypeScript chunk cut by line count is not the same kind of object as a Python chunk cut by span,
and a caller that cannot tell them apart will eventually reason about one as if it were the other.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

#: Characters above which a symbol is split into windows anyway. A 3000-line generated class is a
#: symbol by the parser's reckoning and a haystack by every other measure.
MAX_CHUNK_CHARS = 6000

#: Lines per window in the fallback, and how many of them overlap. The overlap exists so a match
#: that straddles a boundary is whole in at least one window — without it, the one thing a window
#: cut reliably destroys is the thing that spanned the cut.
WINDOW_LINES = 60
WINDOW_OVERLAP = 15

#: Suffixes worth indexing. Deliberately short: a repository's value for retrieval is its source and
#: its prose, and indexing lockfiles and minified bundles spends the budget on text nobody asks
#: about in words.
SOURCE_SUFFIXES = frozenset(
    {".py", ".pyi", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".rs", ".go", ".java", ".rb", ".md"}
)

#: Directories never descended into. Same list as the search engines use, and for the same reason.
SKIP_DIRS = frozenset(
    {
        ".git", ".hg", ".svn", "node_modules", "__pycache__", ".venv", "venv", ".mypy_cache",
        ".pytest_cache", ".ruff_cache", "dist", "build", "target", ".next", ".cache", ".tox",
        "site-packages", ".idea", ".vscode", "coverage", ".gradle",
    }
)


@dataclass(frozen=True)
class Chunk:
    """One retrievable piece of the repository."""

    path: str  # workspace-relative, forward slashes
    #: The symbol's dotted name (`ClassName.method`), or "" for a window chunk.
    symbol: str
    #: "function" | "class" | "window". Carried because a window is a weaker object than a symbol:
    #: it has no name, its boundaries are arbitrary, and a caller ranking the two together should
    #: know which it is holding.
    kind: str
    start_line: int  # 1-based, inclusive
    end_line: int  # inclusive
    text: str

    @property
    def ident(self) -> str:
        """A stable id: the same symbol at the same lines is the same chunk across runs."""
        return f"{self.path}:{self.start_line}-{self.end_line}"

    @property
    def label(self) -> str:
        """What a person would call this. Used in the FTS text so a query naming a symbol hits it."""
        return f"{self.path} {self.symbol}".strip()


def _window(path: str, lines: list[str], first: int, last: int, symbol: str = "") -> list[Chunk]:
    """Cut `lines[first-1:last]` into overlapping windows."""
    out: list[Chunk] = []
    step = max(1, WINDOW_LINES - WINDOW_OVERLAP)
    for start in range(first, last + 1, step):
        end = min(last, start + WINDOW_LINES - 1)
        text = "".join(lines[start - 1 : end])
        if text.strip():
            out.append(Chunk(path, symbol, "window", start, end, text))
        if end >= last:
            break
    return out


def _python_chunks(path: str, source: str) -> list[Chunk] | None:
    """Symbol chunks from Python source, or None when it does not parse.

    None rather than an empty list, and the difference matters: a file with no symbols is
    legitimately empty of them, while a file that failed to parse still has content worth
    retrieving — it just has to be cut a dumber way.
    """
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return None

    lines = source.splitlines(keepends=True)
    chunks: list[Chunk] = []

    def take(node: ast.AST, name: str, kind: str) -> None:
        start = getattr(node, "lineno", 0)
        end = getattr(node, "end_lineno", start)
        if not start:
            return
        # Decorators sit ABOVE `lineno` and are part of what the symbol IS — a `@app.post("/x")`
        # is most of what makes the function below it findable by someone asking about routes.
        decorators = getattr(node, "decorator_list", [])
        if decorators:
            start = min(start, min(getattr(d, "lineno", start) for d in decorators))
        text = "".join(lines[start - 1 : end])
        if not text.strip():
            return
        if len(text) > MAX_CHUNK_CHARS:
            chunks.extend(_window(path, lines, start, end, name))
            return
        chunks.append(Chunk(path, name, kind, start, end, text))

    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            # The class AND its methods. Both, deliberately: a question about the class's purpose is
            # answered by the class header and its docstring, and a question about one behaviour is
            # answered by one method. Indexing only the class buries the method in an average;
            # indexing only the methods loses the thing that says what they are for.
            header_end = min(
                (m.lineno - 1 for m in node.body if isinstance(m, ast.FunctionDef | ast.AsyncFunctionDef)),
                default=node.end_lineno or node.lineno,
            )
            head = "".join(lines[node.lineno - 1 : header_end])
            if head.strip():
                chunks.append(Chunk(path, node.name, "class", node.lineno, header_end, head))
            for member in node.body:
                if isinstance(member, ast.FunctionDef | ast.AsyncFunctionDef):
                    take(member, f"{node.name}.{member.name}", "function")
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            take(node, node.name, "function")

    if not chunks:
        # Module-level code only (a script, a settings file). Real content, no symbols.
        return _window(path, lines, 1, len(lines)) if lines else []
    return chunks


def chunk_source(path: str, source: str) -> list[Chunk]:
    """Cut one file. Never raises; an unparseable file becomes windows."""
    if not source.strip():
        return []
    if path.endswith((".py", ".pyi")):
        parsed = _python_chunks(path, source)
        if parsed is not None:
            return parsed
    lines = source.splitlines(keepends=True)
    return _window(path, lines, 1, len(lines))


def walk(root: Path, *, max_files: int = 5000, max_bytes: int = 500_000) -> list[Chunk]:
    """Every chunk in a workspace.

    Bounded twice, and both bounds are the same judgement: an index that takes minutes to build is
    an index nobody rebuilds, and a stale index is worse than none because it answers confidently
    about code that has moved.
    """
    root = Path(root).resolve()
    out: list[Chunk] = []
    seen = 0
    for path in sorted(root.rglob("*")):
        if seen >= max_files:
            break
        if not path.is_file() or path.suffix.lower() not in SOURCE_SUFFIXES:
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        try:
            if path.stat().st_size > max_bytes:
                continue
            source = path.read_text(encoding="utf-8", errors="strict")
        except (OSError, UnicodeDecodeError):
            continue
        seen += 1
        rel = path.relative_to(root).as_posix()
        out.extend(chunk_source(rel, source))
    return out
