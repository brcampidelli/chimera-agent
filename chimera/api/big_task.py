"""Is this request a BIG task, one whose plan a person should see before anything is edited?

The rule is written down so it can be argued with, and it is deterministic on purpose: a model that
judges its own task "small" is exactly the one that skips the plan, so the decision must not depend
on the judgement it exists to check. A task is big when the request

* names three or more files, or
* asks for a feature or a refactor (the patterns below, EN and PT-BR), and is not a question.

The rule applies only to attended turns, and an explicit ``plan_gate`` from the caller overrides it
in both directions. Both are decided in ``code_api``, not here: this module answers only the
question about the text.

Two things are deliberately NOT counted as files, because they looked like files to the first
version of this rule and were false positives: ``UI/UX`` and ``e/ou`` (a slash between words), and
``Next.js`` / ``v1.2`` / ``e.g`` (a dot between words, where the part after the dot is a real source
extension, or is not one at all). A bare ``Name.js`` counts only when the name is lowercase or the
file exists in the workspace, because a capitalised name followed by ``.js`` is almost always a
product (``Next.js``, ``Vue.js``) and not a file. A ``:line`` or ``:line:col`` suffix is stripped, so
``app.ts:42`` names ``app.ts``.
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path, PurePosixPath

#: How many named files make a task big. Three, not two: a two-file change is routinely a small
#: fix that happens to touch a caller and its test.
FILE_THRESHOLD = 3

#: Source and text extensions that make ``name.ext`` a file mention. Deliberately closed: an
#: open rule ("anything with a dot") is what counted ``v1.2`` and ``e.g``.
FILE_EXTENSIONS = frozenset({
    "py", "pyi", "ts", "tsx", "js", "jsx", "mjs", "cjs", "json", "toml", "yaml", "yml", "md",
    "css", "scss", "html", "sql", "sh", "ps1", "rs", "go", "java", "kt", "swift", "rb", "php",
    "vue", "svelte", "c", "h", "cc", "cpp", "hpp", "cs",
})

#: Extensions that are also the tail of a product name (``Next.js``, ``Vue.js``, ``Node.js``).
#: A bare ``Name.<one of these>`` counts only when the name is lowercase or the file exists.
_AMBIGUOUS_EXTENSIONS = frozenset({"js", "ts", "go", "c", "h", "cs", "rs"})

#: Keyword patterns, matched on the ACCENT-FREE, lowercased message. Each one is an action (a verb)
#: together with the thing it acts on, or a word that only means a feature change: the bare noun
#: ``feature`` is not a trigger, because "the feature works, fix the typo" is a small task. Changing
#: a pattern is a product decision: the list is the rule.
FEATURE_OR_REFACTOR: tuple[str, ...] = (
    # English: a verb that makes something new, then the feature noun (up to two words between).
    r"(?:add|build|create|implement)\s+(?:(?:a|an|the|new|some)\s+){0,2}features?",
    r"implement(?:s|ing)?",
    r"refactor(?:s|ing|ed)?",
    # Portuguese (Brazil), accents removed: adicione/adicionar/implemente/implementar/crie/criar.
    r"(?:adicion(?:e|ar|ando|a)|implement(?:e|ar|ando|a)|cri(?:e|ar|ando)|constru(?:a|ir|indo))"
    r"\s+(?:(?:uma|um|a|o|nova|novo|mais)\s+){0,2}(?:funcionalidades?|recursos?)",
    # An imperative implement verb with an object counts on its own: "implemente a tela X",
    # "implementar a feature de login". The object is any word after the optional article.
    r"implement(?:e|ar|ando|a)\s+(?:(?:o|a|os|as|um|uma|the)\s+)?\w+",
    r"novas?\s+funcionalidades?",
    r"refatorar|refatorando|refatore|refatoracao|reestruturar|reestruture",
)

_KEYWORD_RE = re.compile(r"(?<!\w)(?:" + "|".join(FEATURE_OR_REFACTOR) + r")(?!\w)")

#: A question is asking, not doing: a keyword in a question does not make the task big. Starts
#: checked on the normalized text; a trailing ``?`` anywhere counts.
_QUESTION_START = re.compile(
    r"^\s*(?:why|what|how|explain|explique|por que|porque|o que|como|qual|quais|quando|onde)\b"
)

#: Characters a token is wrapped in when a person writes a file name in prose.
_EDGE_PUNCTUATION = "`'\"()[]{}<>,;:!?"

_LINE_SUFFIX = re.compile(r":\d+(?::\d+)?$")


def is_big_task(message: str, *, workspace: Path | None = None) -> tuple[bool, str]:
    """Decide whether ``message`` is a big task, and say why.

    Returns ``(True, reason)`` or ``(False, "")``. The reason is the human-readable line the turn
    records as ``plan_first_reason``, and the plan card shows: which files were named, or which
    keyword matched. A file counts when it has a path separator and a source extension, when it
    resolves to an existing file under ``workspace`` (relative paths only), or when it is a bare
    ``name.ext`` that passes the rule above. A URL never counts. A question never counts as a
    feature or refactor, but its file mentions still do.
    """
    files = _named_files(message, workspace)
    normalized = _normalize(message)
    keywords: list[str] = []
    if not _is_question(normalized):
        keywords = sorted({m.group(0) for m in _KEYWORD_RE.finditer(normalized)})

    reasons: list[str] = []
    if len(files) >= FILE_THRESHOLD:
        reasons.append(f"names {len(files)} files: {', '.join(files)}")
    if keywords:
        reasons.append(f"a feature or a refactor: {', '.join(repr(k) for k in keywords)}")
    if not reasons:
        return False, ""
    return True, "; ".join(reasons)


def _normalize(text: str) -> str:
    """Lowercase, with accents removed, so ``refatoração`` and ``refatoracao`` are one word."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).lower()


def _is_question(normalized: str) -> bool:
    stripped = normalized.strip()
    return stripped.endswith("?") or bool(_QUESTION_START.match(stripped))


def _named_files(message: str, workspace: Path | None) -> list[str]:
    """The distinct file mentions in ``message``, in the order they first appear."""
    seen: dict[str, str] = {}
    for raw in message.split():
        token = _strip_edges(raw)
        if not token or "://" in token:
            continue
        if _looks_like_file(token, workspace):
            seen.setdefault(token.lower(), token)
    return list(seen.values())


def _strip_edges(raw: str) -> str:
    # A trailing full stop is the end of the sentence, never part of a name: "edit a.py." is a.py.
    # No real file name in this rule ends in a dot.
    token = raw.strip(_EDGE_PUNCTUATION).rstrip(".")
    # `app.ts:42` and `app.ts:42:7` name the file; the line is where the person looked.
    token = _LINE_SUFFIX.sub("", token)
    return token.removeprefix("./")


def _has_extension(token: str) -> bool:
    _, dot, ext = token.rpartition(".")
    return bool(dot) and ext.lower() in FILE_EXTENSIONS


def _looks_like_file(token: str, workspace: Path | None) -> bool:
    if "/" in token or "\\" in token:
        # A path. Counted by its extension, or by existing in the workspace; `UI/UX` has neither.
        if _has_extension(token):
            return True
        return _exists_in(workspace, token)

    if not _has_extension(token):
        return False
    stem, _, ext = token.rpartition(".")
    if not stem or not re.fullmatch(r"[\w][\w.-]*", stem):
        return False
    if ext.lower() in _AMBIGUOUS_EXTENSIONS and stem != stem.lower():
        # `Next.js`, `Vue.go`: a product name. Only a file that really exists makes it one.
        return _exists_in(workspace, token)
    return True


def _exists_in(workspace: Path | None, token: str) -> bool:
    """Whether ``token`` names a file under ``workspace``. Only relative paths, never outside it."""
    if workspace is None:
        return False
    relative = PurePosixPath(token.replace("\\", "/"))
    if relative.is_absolute() or ".." in relative.parts:
        return False
    try:
        return (workspace / Path(*relative.parts)).is_file()
    except OSError:
        return False
