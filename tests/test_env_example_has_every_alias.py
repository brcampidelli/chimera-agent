"""Every environment alias ``config.py`` reads has a line in ``.env.example``.

The example is what an installed user reads to learn which variables exist. Before this test 138 of
241 aliases had no line in it (measured on 2026-11-14), so a variable could be read by the code and
be impossible to discover from the file the onboarding copies. A commented-out line counts: the
point is that the name is findable, and the example's own test already ignores comments when it
checks defaults.
"""

from __future__ import annotations

import re
from pathlib import Path

from chimera.config import Settings

EXEMPLO = Path(__file__).resolve().parents[1] / ".env.example"
_LINHA = re.compile(r"^\s*#?\s*([A-Za-z_][A-Za-z0-9_]*)\s*=", re.MULTILINE)


def _aliases() -> set[str]:
    return {
        campo.validation_alias
        for campo in Settings.model_fields.values()
        if isinstance(campo.validation_alias, str)
    }


def test_every_config_alias_has_a_line_in_the_env_example() -> None:
    presentes = set(_LINHA.findall(EXEMPLO.read_text(encoding="utf-8")))
    faltam = sorted(_aliases() - presentes)
    assert not faltam, (
        f"{len(faltam)} alias(es) de config.py sem linha em .env.example; rode "
        f"`python scripts/gen_env_example.py`: {faltam[:10]}"
    )


def test_the_alias_check_sees_the_settings_it_is_meant_to_cover() -> None:
    """Guard against the check passing because it found nothing to check."""
    aliases = _aliases()
    assert len(aliases) > 200
    assert "CHIMERA_DEFAULT_MODEL" in aliases
