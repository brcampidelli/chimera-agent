"""A `.env` copied from `.env.example` must leave the fusion cast to the user's ladder.

`chimera init` and `chimera doctor --fix` copy `.env.example` to `.env` verbatim. The example carried
``CHIMERA_FUSION_PANEL``/``JUDGE``/``SYNTHESIZER`` as ACTIVE lines set to the code's frontier
defaults, and the fusion factory reads ``model_fields_set`` to tell "the user named a panel" from
"the panel is the default". A line in `.env` is in ``model_fields_set`` whoever wrote it — so every
install that followed the onboarding had "chosen" Opus + GPT-5.5 + Gemini, and ``--fuse`` convened
them under ``CHIMERA_COST_MODE=cheap`` exactly as before the factory existed.

The factory's own tests build ``Settings(**kwargs)`` without a `.env`, so they could not see it.
These build Settings from a copy of the real file, the way an installed user's process does.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from chimera.config import Settings
from chimera.fusion.factory import fusion_config
from chimera.providers.catalog import resolve_tiers

EXEMPLO = Path(__file__).resolve().parents[1] / ".env.example"
_CAST = ("CHIMERA_FUSION_PANEL", "CHIMERA_FUSION_JUDGE", "CHIMERA_FUSION_SYNTHESIZER")


def _settings_from_a_copy(tmp_path: Path, monkeypatch: Any, *extra: str) -> Settings:
    # The process environment outranks the env file, and under the full suite an earlier test may
    # have exported one of these. Clear them so the reading is the file's, which is the question.
    for name in list(os.environ):
        if name.startswith("CHIMERA_FUSION_") or name == "CHIMERA_COST_MODE":
            monkeypatch.delenv(name, raising=False)
    env = tmp_path / ".env"
    # Empty assignments (`KEY=`) are dropped. The repository's convention is that empty means unset,
    # but three float settings (`CHIMERA_GOVERNANCE_BAND_*_AT/_BELOW`) do not honour it yet and a
    # verbatim copy fails to load at all — a separate defect, reported rather than fixed here. Every
    # non-empty line, which is every line that could pin a cast, is kept.
    kept = [
        line
        for line in EXEMPLO.read_text(encoding="utf-8").splitlines()
        if line.lstrip().startswith("#") or not line.strip().endswith("=")
    ]
    body = "\n".join([*kept, *extra]) + "\n"
    env.write_text(body, encoding="utf-8")
    return Settings(_env_file=str(env), CHIMERA_HOME=str(tmp_path / "home"))  # type: ignore[call-arg]  # _env_file and the aliases are runtime-only init kwargs


def test_the_example_assigns_no_fusion_cast() -> None:
    """Assigned in the example means assigned in every scaffolded `.env`, which means "chosen"."""
    active = {
        line.partition("=")[0].strip()
        for line in EXEMPLO.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#") and "=" in line
    }
    assert not active & set(_CAST), (
        f"{sorted(active & set(_CAST))} active in .env.example — a copied .env pins that cast "
        "over the cost mode for a user who never chose it"
    )


def test_a_copied_example_under_a_cheap_cost_mode_fuses_on_the_cheap_ladder(
    tmp_path: Path, monkeypatch: Any
) -> None:
    settings = _settings_from_a_copy(tmp_path, monkeypatch, "CHIMERA_COST_MODE=cheap")
    ladder = resolve_tiers(settings)
    defaults = Settings.model_fields["fusion_panel"].default_factory()  # type: ignore[misc]  # pydantic types the factory as optionally taking data

    config = fusion_config(settings)

    assert "fusion_panel" not in settings.model_fields_set
    assert config.panel == list(dict.fromkeys([ladder.top, ladder.mid, ladder.weak]))
    assert not set(defaults) & set(config.panel)
    assert config.judge == ladder.top and config.synthesizer == ladder.top


def test_a_panel_the_user_writes_into_the_copy_still_wins(tmp_path: Path, monkeypatch: Any) -> None:
    """The fix is in the example, not in the rule: a panel someone writes down is a choice."""
    settings = _settings_from_a_copy(
        tmp_path, monkeypatch, "CHIMERA_COST_MODE=cheap", "CHIMERA_FUSION_PANEL=vendor/a,vendor/b"
    )

    assert fusion_config(settings).panel == ["vendor/a", "vendor/b"]
