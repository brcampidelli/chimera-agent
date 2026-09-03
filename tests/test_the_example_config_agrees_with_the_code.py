"""`.env.example` set a default 20x dearer than the code's, and named two withdrawn models.

Its own header tells the reader to copy it to `.env`, so the assignments in it are not
documentation — they are the configuration a new install runs on. Measured on 2026-09-03:

* ``CHIMERA_DEFAULT_MODEL=openrouter/openai/gpt-5.5`` — 5.00/30.00 per million — while the code's
  default was ``deepseek-chat-v3.1`` at 0.25/0.95. Copying the file ran every cheap task on a model
  **20x dearer on input and 31x on output**, and nothing said so.
* ``CHIMERA_FUSION_PANEL`` named ``claude-opus-4-8`` and ``gemini-3.1-pro``, both withdrawn from
  OpenRouter — a copied example that fails at call time.
* ``CHIMERA_FUSION_JUDGE`` was the panel's own first member, which is the judge grading its own
  answer. The comment above ``_DEFAULT_JUDGE`` in ``chimera/config.py`` records that exact defect
  being fixed once already; the example file had quietly kept a copy of it.

None of it was caught by anything, because the drift gate that checks models against the live index
reads ``Settings()`` — and ``Settings()`` never reads this file in a test process. So this holds the
narrower, offline invariant: an ACTIVE assignment in the example must agree with the code default it
sets. A commented-out suggestion is free to differ, which is what makes it a suggestion.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from chimera.config import Settings

EXEMPLO = Path(__file__).resolve().parents[1] / ".env.example"


def _ativas() -> dict[str, str]:
    """Only the uncommented ``KEY=value`` lines — the ones a copy actually applies."""
    fora = {}
    for linha in EXEMPLO.read_text(encoding="utf-8").splitlines():
        crua = linha.strip()
        if not crua or crua.startswith("#") or "=" not in crua:
            continue
        chave, _, valor = crua.partition("=")
        fora[chave.strip()] = valor.strip()
    return fora


def _declarado(campo: str) -> Any:
    """The default the CODE declares, not what this process happens to be configured with.

    `Settings()` reads the environment, and under the full suite some earlier test has exported a
    `CHIMERA_*` — so an instance answers "what is in force here", which is a different question and
    made this test pass alone and fail in the suite. The example file is compared against what the
    code ships, so the field default is the right reading either way.
    """
    campo_info = Settings.model_fields[campo]
    if campo_info.default_factory is not None:
        return campo_info.default_factory()
    return campo_info.default


def test_the_example_config_agrees_with_the_code() -> None:
    ativas = _ativas()
    esperado = {
        "CHIMERA_DEFAULT_MODEL": _declarado("default_model"),
        "CHIMERA_FUSION_PANEL": ",".join(_declarado("fusion_panel")),
        "CHIMERA_FUSION_JUDGE": _declarado("fusion_judge"),
        "CHIMERA_FUSION_SYNTHESIZER": _declarado("fusion_synthesizer"),
    }
    divergentes = {
        k: (ativas[k], v) for k, v in esperado.items() if k in ativas and ativas[k] != v
    }
    assert not divergentes, (
        "the example file is copied to .env as-is, so an active assignment that disagrees with the "
        f"code silently changes what a new install runs: {divergentes}"
    )


def test_the_example_does_not_make_the_judge_a_panellist() -> None:
    """The judge grading its own answer is a defect the code fixed and the example reintroduced."""
    ativas = _ativas()
    painel = [m.strip() for m in ativas.get("CHIMERA_FUSION_PANEL", "").split(",") if m.strip()]
    juiz = ativas.get("CHIMERA_FUSION_JUDGE", "")
    if not painel or not juiz:
        return
    assert juiz not in painel, f"{juiz} judges the panel it sits on"

    def fornecedor(slug: str) -> str:
        partes = slug.split("/")
        return partes[1] if len(partes) > 2 else (partes[0] if partes else "")

    assert fornecedor(juiz) not in {fornecedor(m) for m in painel}, (
        "same vendor as a panellist — the kinship rule the fusion engine enforces"
    )


def test_every_active_model_slug_is_shaped_like_one() -> None:
    """Offline shape check. Whether a slug still EXISTS is the live gate's job, not this one's."""
    ativas = _ativas()
    slugs: list[str] = []
    for chave, valor in ativas.items():
        if "MODEL" in chave or "PANEL" in chave or "JUDGE" in chave or "SYNTHESIZER" in chave:
            slugs += [m.strip() for m in valor.split(",") if m.strip()]
    for slug in slugs:
        assert re.fullmatch(r"[a-z0-9._-]+(/[A-Za-z0-9._:-]+){1,2}", slug), slug
        assert ":free" not in slug, (
            f"{slug} is a :free slug — the file's own comment says these rate-limit under load and "
            "make fusion degrade silently, so the example must not ship one"
        )


def test_the_withdrawn_slugs_that_were_here_do_not_come_back() -> None:
    """Named, not inferred: these two were in the file and are gone from OpenRouter.

    Scoped to the ACTIVE assignments, not to the text. The comment above those lines names both
    slugs on purpose — it is the record of what was wrong and why — and a check over the whole file
    would forbid writing that history down. Which it did, on the first run of this test.
    """
    ativas = " ".join(_ativas().values())
    for morto in ("claude-opus-4-8", "gemini-3.1-pro"):
        # `gemini-3.1-pro-preview` is live and legitimate; the withdrawn one is the bare name, so
        # match it as a whole slug rather than as a prefix of its own successor.
        for slug in (m for v in ativas.split() for m in v.split(",")):
            assert not slug.strip().endswith(morto), (
                f"{morto!r} was withdrawn; an example naming it fails at call time"
            )
