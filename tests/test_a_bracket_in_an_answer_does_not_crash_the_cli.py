"""Model text printed by the CLI is escaped, so a reply containing ``[/]`` cannot crash it.

``console`` in ``chimera/cli/commands/_shared.py`` is a Rich console with markup on, and Rich parses ``[/]`` in
a string as a closing tag with nothing to close: ``MarkupError``, after the turn was already paid
for and with the answer lost. ``interface/render.py`` documents exactly this and escapes every
reply the REPLs print — but the one-shot commands (``run``, ``agent``, ``deliver``, ``fuse``,
``orchestrate``, ``solve``, ``crew``) printed the model's text raw. Code answers carry brackets as a
matter of course (``arr[/]`` does not occur, but ``[bold]`` in a Rich snippet and ``[/INST]`` in a
prompt-format question do), so this is the common case, not a curiosity.

The CliRunner tests drive two commands end to end with a stub model; the structural test holds the
rest, whose setup (worktrees, crews) is out of proportion to a one-line print.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from chimera.cli.main import app
from chimera.config import get_settings
from tests.cli_sources import cli_command_files

REPLY = "x [/] y [bold]z"
runner = CliRunner()


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_run_prints_a_bracketed_reply_verbatim(monkeypatch: pytest.MonkeyPatch) -> None:
    class Gateway:
        def quick(self, *_a: Any, **_k: Any) -> str:
            return REPLY

    monkeypatch.setattr("chimera.providers.LLMGateway", Gateway)
    result = runner.invoke(app, ["run", "hi"])

    assert result.exception is None, repr(result.exception)
    assert REPLY in result.stdout


def test_deliver_prints_a_bracketed_document_verbatim(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []

    def fake_produce(*_a: Any, **_k: Any) -> str:
        seen.append("called")
        return REPLY

    monkeypatch.setattr("chimera.providers.LLMGateway", lambda *a, **k: object())
    monkeypatch.setattr("chimera.deliver.produce_deliverable", fake_produce)
    result = runner.invoke(app, ["deliver", "a note"])

    assert seen, "the stub was not reached, so this would prove nothing"
    assert result.exception is None, repr(result.exception)
    assert REPLY in result.stdout


def _cli_nodes() -> list[tuple[Path, ast.AST]]:
    """Every AST node of every CLI command file — the commands left main.py in S30-70."""
    return [
        (path, node)
        for path in cli_command_files()
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
    ]


_MODEL_TEXT_NAMES = {"answer", "document"}
_MODEL_TEXT_ATTRS = {"answer", "content", "final"}


def test_no_command_prints_model_text_unescaped() -> None:
    """``console.print(<model text>)`` with nothing between them is the crash, on any command."""
    offenders = []
    for path, node in _cli_nodes():
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "print"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "console"
            and len(node.args) == 1
        ):
            continue
        arg = node.args[0]
        raw = (isinstance(arg, ast.Name) and arg.id in _MODEL_TEXT_NAMES) or (
            isinstance(arg, ast.Attribute) and arg.attr in _MODEL_TEXT_ATTRS
        )
        if raw:
            offenders.append(f"{path.name}:{node.lineno} {ast.unparse(arg)}")
    assert not offenders, "escape these with rich.markup.escape:\n" + "\n".join(offenders)


def test_fuse_show_panel_prints_bracketed_panel_judge_and_final(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``Panel(str)`` parses markup like ``console.print`` does; the escaped plain branch of ``fuse``
    left the ``--show-panel`` branch crashing on the same ``[/]`` after the fusion was paid for."""
    from types import SimpleNamespace

    class Engine:
        def __init__(self, *_a: Any, **_k: Any) -> None:
            pass

        def run(self, _messages: Any) -> Any:
            member = SimpleNamespace(error=None, content=REPLY, model="vendor/a")
            return SimpleNamespace(
                total_tokens=lambda: None,
                early_stopped=False,
                panel=[member],
                judge_analysis=REPLY,
                final=REPLY,
                # The CLI labels a panel fallback (S30-02), so the fake says how it was aggregated.
                aggregation="synth",
            )

    monkeypatch.setattr("chimera.fusion.FusionEngine", Engine)
    monkeypatch.setattr("chimera.providers.LLMGateway", lambda *a, **k: object())
    result = runner.invoke(app, ["fuse", "hi", "--show-panel"])

    assert result.exception is None, repr(result.exception)
    assert result.stdout.count(REPLY) == 3


def test_no_panel_wraps_text_it_did_not_escape() -> None:
    """``Panel(<anything but a literal or escape(...)>)`` is the same crash one call further in.

    ``Markdown(...)`` is deliberately not held: Rich renders its source as Markdown and does not
    parse console markup in it (``Markdown("x [/] y")`` prints verbatim).
    """
    offenders = []
    for path, node in _cli_nodes():
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
            continue
        if node.func.id != "Panel" or not node.args:
            continue
        arg = node.args[0]
        safe = isinstance(arg, ast.Constant) or (
            isinstance(arg, ast.Call) and isinstance(arg.func, ast.Name) and arg.func.id == "escape"
        )
        if not safe:
            offenders.append(f"{path.name}:{node.lineno} Panel({ast.unparse(arg)})")
    assert not offenders, "wrap with rich.markup.escape:\n" + "\n".join(offenders)
