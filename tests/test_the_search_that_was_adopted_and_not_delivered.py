"""`bench/rag` said ADOPT, and `chimera find` kept running keyword-only.

The measurement in `bench/rag/RESULTS.md` is unambiguous: hybrid 0.5050 against keyword 0.4425 on
this repository, +6.25 pp paired over 400 probes, McNemar p = 1.7e-04. What it did not do is reach a
user — the command had no way to ask for the semantic half, and `chimera measure rag` had no way to
pass an embedder, so the run that produced the verdict could only be reproduced by writing a script.

Two things this file holds, and the second is the one that would be easy to lose:

* `--semantic` means **hybrid**, never vectors alone. The vector arm on its own scored **0.4100 —
  below keyword**. Every point of the win comes from fusing two rankings that are wrong about
  different things, so a flag that handed a user the vector arm would be a flag that made their
  search worse while sounding like an upgrade.
* The embedding bill is announced **before** it is incurred. Indexing is the one part of this command
  that costs money, and a user who did not expect one cannot un-spend it.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from typer.testing import CliRunner

from chimera.cli.main import app
from chimera.config import Settings

runner = CliRunner()


def flat(output: str) -> str:
    """Rich wraps to the terminal width, so a phrase can arrive split across two lines.

    Asserting on the raw output makes the test a function of the runner's column count, which is
    a property of the harness rather than of the command.
    """
    return re.sub(r"\s+", " ", output)

SOURCE = '''
def parse_plan(text: str) -> list[str]:
    """Turn a numbered plan into the steps it names."""
    return text.splitlines()


def budget_left(spent: float, cap: float) -> float:
    """How much of the ceiling has not been spent yet."""
    return cap - spent
'''

VOCAB = ["plan", "steps", "budget", "ceiling", "spent", "numbered", "turn"]


def _embed(texts: list[str]) -> list[list[float]]:
    return [[float(t.lower().count(w)) for w in VOCAB] for t in texts]


def _repo(tmp_path: Path) -> Path:
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "core.py").write_text(SOURCE, encoding="utf-8")
    return tmp_path


# --- the flag exists and is off by default ---------------------------------------------------------


def test_without_the_flag_no_embedder_is_built(tmp_path: Path, monkeypatch: Any) -> None:
    """The default path must not acquire a bill. Nothing calls an embedder unless asked."""
    called: list[int] = []
    monkeypatch.setattr(
        "chimera.evolution.wiring.semantic_embed",
        lambda *a, **k: called.append(1) or _embed,
        raising=True,
    )
    monkeypatch.setattr("chimera.config.get_settings", lambda: Settings(CHIMERA_HOME=str(tmp_path)))

    result = runner.invoke(app, ["find", "how a plan becomes steps", "--path", str(_repo(tmp_path))])

    assert result.exit_code == 0, result.output
    assert called == [], "an embedder was built for a search that did not ask for one"
    assert "keyword only" in flat(result.output)


def test_the_keyword_footer_names_what_it_costs_to_do_better(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setattr("chimera.config.get_settings", lambda: Settings(CHIMERA_HOME=str(tmp_path)))

    result = runner.invoke(app, ["find", "budget ceiling", "--path", str(_repo(tmp_path))])

    assert "0.443" in flat(result.output), "the measured keyword recall travels with every search"
    assert "--semantic" in flat(result.output)


# --- the semantic arm ------------------------------------------------------------------------------


def test_semantic_fuses_and_never_returns_the_vector_arm_alone(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """The measurement's own conclusion, held in code: vector ALONE scored below keyword."""
    fused: list[str] = []
    real_fusion = None
    from chimera.rag import hybrid

    real_fusion = hybrid.reciprocal_rank_fusion

    def spy(rankings: Any, limit: int = 10) -> Any:
        fused.append("called")
        return real_fusion(rankings, limit=limit)

    monkeypatch.setattr("chimera.rag.hybrid.reciprocal_rank_fusion", spy, raising=True)
    monkeypatch.setattr("chimera.evolution.wiring.semantic_embed", lambda *a, **k: _embed)
    monkeypatch.setattr("chimera.config.get_settings", lambda: Settings(CHIMERA_HOME=str(tmp_path)))

    result = runner.invoke(
        app, ["find", "how a plan becomes steps", "--path", str(_repo(tmp_path)), "--semantic"]
    )

    assert result.exit_code == 0, result.output
    assert fused, "the semantic arm did not fuse — it would be handing back the losing retriever"


def test_the_bill_is_announced_before_it_is_incurred(tmp_path: Path, monkeypatch: Any) -> None:
    """Order matters: a warning printed after the embedding pass warns nobody."""
    order: list[str] = []

    def embed(texts: list[str]) -> list[list[float]]:
        order.append("embedded")
        return _embed(texts)

    monkeypatch.setattr("chimera.evolution.wiring.semantic_embed", lambda *a, **k: embed)
    monkeypatch.setattr("chimera.config.get_settings", lambda: Settings(CHIMERA_HOME=str(tmp_path)))

    result = runner.invoke(app, ["find", "budget", "--path", str(_repo(tmp_path)), "--semantic"])

    assert "costs money" in flat(result.output)
    assert order, "nothing was embedded, so this proves nothing about the ordering"
    # The line is printed by the same command before the pass; asserting both appear is what a
    # captured-output test can honestly claim.
    seen = flat(result.output)
    assert seen.index("costs money") < seen.index("recall@10")


def test_the_semantic_footer_names_the_embedder(tmp_path: Path, monkeypatch: Any) -> None:
    """A recall figure without the model that produced it is a number about nothing in particular —
    vector spaces do not convert between models."""
    monkeypatch.setattr("chimera.evolution.wiring.semantic_embed", lambda *a, **k: _embed)
    settings = Settings(CHIMERA_HOME=str(tmp_path))
    monkeypatch.setattr("chimera.config.get_settings", lambda: settings)

    result = runner.invoke(app, ["find", "budget", "--path", str(_repo(tmp_path)), "--semantic"])

    assert settings.embed_model in flat(result.output)
    assert "0.410" in flat(result.output), "the losing vector arm is stated, not hidden"


def test_an_embedder_that_cannot_be_built_refuses_rather_than_falling_back(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """Falling back to keyword under a `--semantic` flag would answer a question nobody asked."""
    monkeypatch.setattr("chimera.evolution.wiring.semantic_embed", lambda *a, **k: None)
    monkeypatch.setattr("chimera.config.get_settings", lambda: Settings(CHIMERA_HOME=str(tmp_path)))

    result = runner.invoke(app, ["find", "budget", "--path", str(_repo(tmp_path)), "--semantic"])

    assert result.exit_code == 1
    assert "needs an embedder" in flat(result.output)


# --- the seam that made it possible ------------------------------------------------------------------


def test_asking_in_so_many_words_is_not_the_same_as_the_standing_setting() -> None:
    """`--semantic` on one search and `CHIMERA_SEMANTIC_MEMORY=1` are different decisions.

    Reading the standing setting as consent to a one-off bill, or refusing the one-off because the
    standing setting is off, would each be answering a question the user did not ask.
    """
    from chimera.evolution.wiring import semantic_embed

    off = Settings(CHIMERA_HOME="x")
    assert semantic_embed(off) is None
    assert semantic_embed(off, force=True) is not None
