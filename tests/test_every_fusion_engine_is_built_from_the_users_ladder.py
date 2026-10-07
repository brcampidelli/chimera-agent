"""Every fusion engine the CLI and the API build comes from one factory, not a bare constructor.

`api/roles.py` documents a measured bug: ``FusionEngine(gateway)`` with no config falls through to
``FusionConfig.from_settings()``, whose ``CHIMERA_FUSION_PANEL`` default is three frontier models.
Role fusion and the cascade's top rung were fixed by routing them through ``fusion_for_role``; the
other surfaces kept the bare form. So ``--fuse`` on deliver, agent, chat, tui, serve, solve, crew,
the ``/task`` command, the desktop's "Fuse this turn" and the API's fused solve all convened Opus +
GPT-5.5 + Gemini whatever cost mode the user had picked — the bug the docstrings call measured,
still shipping on seventeen CLI call sites and three API ones.

The structural test below is the guard that a per-site fix lacks: a new surface written by copying
an old one cannot reintroduce the bare form without turning it red. Two sites are allowed by name,
because they exist to measure the default panel and changing them would change what they measure.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

from chimera.config import _DEFAULT_PANEL, Settings
from chimera.fusion.factory import fusion_config, fusion_engine
from chimera.providers.catalog import resolve_tiers
from tests.cli_sources import cli_command_files

_ROOT = Path(__file__).resolve().parents[1] / "chimera"

# (file relative to chimera/, enclosing function) -> why the default panel is the point there.
_ALLOWED = {
    ("cli/commands/bench.py", "cascade_bench"): "measures the cascade against the default panel on purpose",
    ("cli/commands/bench.py", "bench"): "measures fusion against single models on the default panel",
}

# Read from the code, not spelled out. This was a hand-typed set naming `claude-opus-4-8` and
# `gemini-3.1-pro` — slugs retired from the default on 2026-08-18 — so the intersection below could
# never be non-empty, and the assertion stayed green even with the factory returning the frontier.
_FRONTIER = set(_DEFAULT_PANEL)


def _settings(**over: Any) -> Settings:
    return Settings(CHIMERA_HOME="/tmp/chimera-fusion-factory", **over)


def _bare_constructions(path: Path) -> list[tuple[str, int]]:
    """``FusionEngine(x)`` with no config, or ``FusionConfig.from_settings(...)``, and their function.

    ``from_settings`` is the bare builder by another name: ``chimera fuse`` built its config with it
    and handed it to ``FusionEngine(gateway, config)``, which passed the constructor check while
    convening the frontier panel under every cost mode.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: list[tuple[str, int]] = []

    def visit(node: ast.AST, func: str) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            func = node.name if func == "<module>" else func
        if isinstance(node, ast.Call):
            name = node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", "")
            has_config = len(node.args) >= 2 or any(k.arg == "config" for k in node.keywords)
            if name == "FusionEngine" and not has_config:
                found.append((func, node.lineno))
            target = node.func.value if isinstance(node.func, ast.Attribute) else None
            if name == "from_settings" and isinstance(target, ast.Name) and target.id == "FusionConfig":
                found.append((func, node.lineno))
        for child in ast.iter_child_nodes(node):
            visit(child, func)

    visit(tree, "<module>")
    return found


def test_no_cli_or_api_surface_builds_a_bare_fusion_engine() -> None:
    offenders = []
    for folder in ("cli", "api"):
        for path in sorted((_ROOT / folder).rglob("*.py")):
            rel = path.relative_to(_ROOT).as_posix()
            for func, line in _bare_constructions(path):
                if (rel, func) not in _ALLOWED:
                    offenders.append(f"{rel}:{line} in {func}()")
    assert not offenders, "bare FusionEngine(...) — use chimera.fusion.factory:\n" + "\n".join(offenders)


def test_the_allowlist_names_sites_that_still_exist() -> None:
    """An allowlist entry whose site is gone is a hole waiting for the next bare constructor."""
    present = {
        (path.relative_to(_ROOT).as_posix(), func)
        for path in cli_command_files()
        for func, _ in _bare_constructions(path)
    }
    assert set(_ALLOWED) <= present


def test_the_factory_panel_under_a_cheap_cost_mode_is_the_cheap_ladder() -> None:
    settings = _settings(CHIMERA_COST_MODE="cheap")
    ladder = resolve_tiers(settings)  # type: ignore[arg-type]

    panel = fusion_engine(object(), settings).config.panel

    assert set(panel) <= {ladder.weak, ladder.mid, ladder.top}
    assert not _FRONTIER & set(panel)


def test_the_blind_panel_setting_reaches_a_role_engine() -> None:
    """``fusion_for_role`` rebuilt FusionConfig field by field and left out ``blind_panel``, so
    ``CHIMERA_FUSION_BLIND_PANEL=false`` was silently ignored on every role and cascade path while
    the bare path — the one with the wrong panel — honoured it."""
    from chimera.api.roles import fusion_for_role

    settings = _settings(CHIMERA_FUSION_BLIND_PANEL="false")

    assert fusion_for_role(object(), settings).config.blind_panel is False  # type: ignore[attr-defined]
    assert fusion_config(settings).blind_panel is False


def test_every_setting_that_from_settings_reads_reaches_the_factory() -> None:
    """The two builders drifted once; this pins them to the same fields so they cannot again."""
    from dataclasses import fields

    from chimera.fusion.engine import FusionConfig

    settings = _settings(
        CHIMERA_FUSION_PANEL="vendor/a,vendor/b",
        CHIMERA_FUSION_JUDGE="vendor/j",
        CHIMERA_FUSION_SYNTHESIZER="vendor/s",
        CHIMERA_FUSION_MODE="full",
        CHIMERA_FUSION_PROBE_K="3",
        CHIMERA_FUSION_AGREEMENT="0.6",
        CHIMERA_FUSION_TASK_TYPED="true",
        CHIMERA_FUSION_BLIND_PANEL="false",
        CHIMERA_FUSION_PANEL_TEMPS="0.1,0.9",
    )
    built = fusion_config(settings)
    expected = FusionConfig.from_settings(settings)

    assert {f.name: getattr(built, f.name) for f in fields(FusionConfig)} == {
        f.name: getattr(expected, f.name) for f in fields(FusionConfig)
    }


def test_the_config_screen_reports_the_panel_that_will_run(monkeypatch: Any) -> None:
    """The desktop seeds its "standing cast" from ``/api/config``. Once the engines draw the ladder,
    a screen still showing ``settings.fusion_panel`` would name three models nobody convenes."""
    from chimera.api import config_api

    settings = _settings(CHIMERA_COST_MODE="cheap")
    block = config_api._fusion_block(settings)

    assert block["panel"] == fusion_config(settings).panel
    assert block["judge"] == fusion_config(settings).judge


def test_chimera_fuse_convenes_the_ladder_under_a_cheap_cost_mode(monkeypatch: Any) -> None:
    """``chimera fuse`` built ``FusionConfig.from_settings()`` and passed it in, so the constructor
    check above could not see it — and it convened the frontier panel while ``chimera models`` told
    the user ``--fuse`` convenes the ladder."""
    from types import SimpleNamespace

    from typer.testing import CliRunner

    from chimera.cli.main import app
    from chimera.config import get_settings

    for name in ("CHIMERA_FUSION_PANEL", "CHIMERA_FUSION_JUDGE", "CHIMERA_FUSION_SYNTHESIZER"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("CHIMERA_COST_MODE", "cheap")
    monkeypatch.setenv("CHIMERA_HOME", "/tmp/chimera-fusion-factory")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    get_settings.cache_clear()
    seen: list[Any] = []

    class Engine:
        def __init__(self, _gateway: Any, config: Any) -> None:
            seen.append(config)

        def run(self, _messages: Any) -> Any:
            # `aggregation`: the CLI labels a panel fallback (S30-02), so a fake trace says how it
            # was aggregated, like the real one always does.
            return SimpleNamespace(
                total_tokens=lambda: None, early_stopped=True, final="ok", panel=[], aggregation="synth"
            )

    monkeypatch.setattr("chimera.fusion.FusionEngine", Engine)
    monkeypatch.setattr("chimera.providers.LLMGateway", lambda *a, **k: object())
    try:
        result = CliRunner().invoke(app, ["fuse", "hi"])
        ladder = resolve_tiers(get_settings())
    finally:
        get_settings.cache_clear()

    assert result.exception is None, repr(result.exception)
    assert seen, "the stub engine was not reached, so this would prove nothing"
    assert set(seen[0].panel) <= {ladder.top, ladder.mid, ladder.weak}
    assert not _FRONTIER & set(seen[0].panel)
