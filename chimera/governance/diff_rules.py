"""A deterministic rule that reads a DIFF: a new dangerous sink called with a non-literal argument.

`policy.py` reads shell commands and free text; nothing in the kernel reads what a patch adds. SWEADV
(arXiv 2609.15963) measured why that matters: an adversarial issue yields a patch that is correct
*and* malicious in 51.7% of cases, and a test gate cannot see it — the tests pass. The shape that
survives the tests is a sink that was not there before, fed by something that is not a literal:
`subprocess.run(cmd)` where `cmd` came from the issue, `pickle.loads(blob)`, `yaml.load(text)`,
`requests.post(url, data=…)`.

The rule is a REVIEW and never a BLOCK, by the same invariant as `policy.py`: adding a sink with a
variable argument is ordinary work often enough (a CLI wrapper, a cache file) that a hard stop
would teach people to click through. What it buys is that a person sees the line before the
attempt counts as done — the run pauses for sign-off through the same checkpoint `pause_on_taint`
uses, and only on a surface that opted in. Its false-alarm rate was measured on the accepted patch
history before it was wired (`bench/test_gate_two_sided`), and the flags land on the attempt
receipt whatever the surface chose.

No model, no network: `ast` over the post-patch file, restricted to the lines the diff added.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass

#: Callee dotted names (as written, after the usual imports) that reach the outside world or
#: execute data. Bare names cover `from subprocess import run` style imports imperfectly — a
#: caller that aliases `run` as `go` is not seen — which is the same limit every lexical layer here
#: declares, and the taint ledger's job when it matters.
SINKS: frozenset[str] = frozenset({
    "subprocess.run", "subprocess.call", "subprocess.check_call", "subprocess.check_output",
    "subprocess.Popen", "os.system", "os.popen", "os.execv", "os.execvp", "os.spawnl",
    "eval", "exec", "compile",
    "pickle.load", "pickle.loads", "marshal.loads", "shelve.open",
    "yaml.load", "yaml.unsafe_load", "yaml.full_load",
    "hashlib.md5", "hashlib.sha1",
    "requests.get", "requests.post", "requests.put", "requests.request",
    "urllib.request.urlopen", "urllib.request.Request", "httpx.get", "httpx.post",
    "socket.socket.connect", "socket.create_connection",
    "shutil.rmtree", "os.remove", "os.unlink", "os.rmdir", "os.removedirs",
    "open",
})

#: `open` is a sink only when it writes; a variable path read is ordinary work.
_WRITE_MODES = re.compile(r"[wax+]")

_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


@dataclass(frozen=True)
class DiffFlag:
    path: str
    line: int
    sink: str
    argument: str  # the first argument's source, as a person would want to see it

    def render(self) -> str:
        return f"{self.path}:{self.line}: {self.sink}({self.argument}) — a new sink with a non-literal argument"


def added_lines(patch: str) -> set[int]:
    """Line numbers, in the NEW file, of every `+` line of a unified-diff body (hunks only)."""
    out: set[int] = set()
    new_line = 0
    for raw in patch.splitlines():
        match = _HUNK.match(raw)
        if match:
            new_line = int(match.group(1))
            continue
        if raw.startswith("+++") or raw.startswith("---"):
            continue
        if raw.startswith("+"):
            out.add(new_line)
            new_line += 1
        elif raw.startswith("-"):
            continue
        else:
            new_line += 1
    return out


def _dotted(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    return ""


def _is_literal(node: ast.AST) -> bool:
    if isinstance(node, ast.Constant):
        return True
    if isinstance(node, ast.JoinedStr):  # an f-string with no interpolation is a literal
        return all(isinstance(v, ast.Constant) for v in node.values)
    if isinstance(node, (ast.Tuple, ast.List)):
        return all(_is_literal(e) for e in node.elts)
    return False


def _open_writes(call: ast.Call) -> bool:
    mode: ast.AST | None = call.args[1] if len(call.args) > 1 else None
    for kw in call.keywords:
        if kw.arg == "mode":
            mode = kw.value
    if mode is None:
        return False
    if isinstance(mode, ast.Constant) and isinstance(mode.value, str):
        return bool(_WRITE_MODES.search(mode.value))
    return True  # a non-literal mode is itself the shape this rule looks for


def _safe_yaml(call: ast.Call) -> bool:
    for kw in call.keywords:
        if kw.arg == "Loader" and _dotted(kw.value).endswith(("SafeLoader", "CSafeLoader", "BaseLoader")):
            return True
    return False


def flag_source(path: str, source: str, new_lines: set[int] | None = None) -> list[DiffFlag]:
    """Sinks with a non-literal first argument in ``source``; restricted to ``new_lines`` when given."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    out: list[DiffFlag] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        # Any line of the call, not just its first: a patch that turns an existing sink's literal
        # argument into a variable touches the argument's line, and that is the shape to see.
        span = range(node.lineno, (getattr(node, "end_lineno", None) or node.lineno) + 1)
        if new_lines is not None and not any(ln in new_lines for ln in span):
            continue
        name = _dotted(node.func)
        if name not in SINKS:
            continue
        if not node.args:
            continue
        first = node.args[0]
        if _is_literal(first):
            continue
        if name == "open" and not _open_writes(node):
            continue
        if name == "yaml.load" and _safe_yaml(node):
            continue
        argument = ast.get_source_segment(source, first) or type(first).__name__
        out.append(DiffFlag(path=path, line=node.lineno, sink=name, argument=argument[:80]))
    return out


def flag_patch(path: str, patch: str, after: str) -> list[DiffFlag]:
    """The rule on one file: the post-patch source, restricted to the lines the patch added."""
    lines = added_lines(patch)
    if not lines:
        return []
    return flag_source(path, after, lines)


def flag_change(path: str, before: str, after: str) -> list[DiffFlag]:
    """The rule on one file given both texts — the diff is computed here in full, so a receipt's
    clipped ``patch`` never decides which lines count as added."""
    import difflib

    if before == after or not path.endswith(".py"):
        return []
    patch = "\n".join(difflib.unified_diff(before.splitlines(), after.splitlines(), lineterm="", n=0))
    return flag_patch(path, patch, after)


def flag_snapshots(before: dict[str, str], after: dict[str, str]) -> list[DiffFlag]:
    """Every flag over a workspace change: {relative path: text} before and after."""
    out: list[DiffFlag] = []
    for path in sorted(after):
        out.extend(flag_change(path, before.get(path, ""), after[path]))
    return out
