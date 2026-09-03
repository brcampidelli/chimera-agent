"""A `-preview` slug shipped as a default, and nothing was watching.

Found by surveying the live index on 2026-09-03: `_DEFAULT_PANEL` carried
`openrouter/google/gemini-3.1-pro-preview`. A preview is a model its vendor may withdraw without
notice, and a default is the one setting a user never chose — so the failure lands on somebody who
never opted into the risk, as a call that stops working for a reason nothing on their screen
explains.

The same family covers two more shapes the survey turned up in the catalogue:

* `~vendor/model-latest` aliases, thirteen of them, which do not vanish — they silently become a
  different model. For a default that is worse, because nothing fails and the behaviour changes.
* `:free` slugs, which this repo has already been bitten by twice: the catalogue's own notes record
  `llama-3.3-70b-instruct:free` and `gpt-oss-20b:free` being withdrawn a fortnight apart.

None of that is a reason to keep them out of the CATALOGUE. A user who picks a preview on purpose
has made a choice and can unmake it. What this holds is narrower: the settings nobody chose.
"""

from __future__ import annotations

import pytest

from chimera.config import Settings
from chimera.providers.catalog import PROVIDERS, _PRESETS

#: Substrings that mark a slug as one a vendor may withdraw or repoint under you.
FRAGIL = ("preview", "-beta", "-exp", ":free", "-latest")


def _declarado(campo: str):
    """The default the CODE ships, not what this process is configured with."""
    info = Settings.model_fields[campo]
    return info.default_factory() if info.default_factory is not None else info.default


def _fragilidades(slug: str) -> list[str]:
    if not slug:
        return []
    achados = [f for f in FRAGIL if f in slug]
    if slug.startswith("~"):
        achados.append("~alias")
    return achados


def _todos_os_padroes() -> dict[str, str]:
    """Every slug a user gets without choosing it, keyed by where it comes from."""
    tudo: dict[str, str] = {
        "default_model": _declarado("default_model"),
        "fusion_judge": _declarado("fusion_judge"),
        "fusion_synthesizer": _declarado("fusion_synthesizer"),
    }
    for i, m in enumerate(_declarado("fusion_panel")):
        tudo[f"fusion_panel[{i}]"] = m
    for i, m in enumerate(_declarado("transfer_panel")):
        tudo[f"transfer_panel[{i}]"] = m
    for modo, escada in _PRESETS.items():
        for papel in ("weak", "mid", "top"):
            tudo[f"_PRESETS[{modo}].{papel}"] = getattr(escada, papel)
    for p in PROVIDERS:
        tudo[f"PROVIDERS[{p.label}].default_model"] = p.default_model
    return tudo


def test_no_default_is_a_model_that_may_be_withdrawn_or_repointed() -> None:
    culpados = {
        onde: (slug, _fragilidades(slug))
        for onde, slug in _todos_os_padroes().items()
        if _fragilidades(slug)
    }
    assert not culpados, (
        "a default is the setting a user never chose, so a slug the vendor may withdraw or repoint "
        f"puts the failure on somebody who did not opt into it: {culpados}"
    )


@pytest.mark.parametrize("onde,slug", sorted(_todos_os_padroes().items()))
def test_every_default_is_a_real_slug_shape(onde: str, slug: str) -> None:
    """Cheap shape check — a default that is empty or half-written fails at call time, not here."""
    assert slug, f"{onde} is empty"
    assert "/" in slug, f"{onde} = {slug!r} is not a provider-qualified slug"
    assert not slug.strip() != slug, f"{onde} = {slug!r} has surrounding whitespace"


def test_the_check_would_actually_have_caught_the_one_that_shipped() -> None:
    """The guard is only worth having if it fires on the case that produced it.

    `gemini-3.1-pro-preview` sat in `_DEFAULT_PANEL` through several releases. Asserting the rule
    against today's clean state proves nothing about whether the rule can see a violation.
    """
    assert _fragilidades("openrouter/google/gemini-3.1-pro-preview") == ["preview"]
    assert _fragilidades("~google/gemini-flash-latest") == ["-latest", "~alias"]
    assert _fragilidades("openrouter/qwen/qwen3-coder:free") == [":free"]
    assert _fragilidades("openrouter/deepseek/deepseek-v4-flash-0731") == []


def test_the_judge_is_not_a_panelist_nor_a_vendor_mate_of_one() -> None:
    """Restated here because the judge MOVED, and the rule it has to keep is easy to lose in a swap.

    `test_fusion_role_independence` owns this property; this asserts it about the shipped values
    after the change, so a future swap that quietly breaks independence fails in the file that
    changed it too.
    """
    juiz = _declarado("fusion_judge")
    painel = _declarado("fusion_panel")
    assert juiz not in painel, "the judge would be grading its own answer"
    vendor = juiz.split("/")[1] if juiz.count("/") >= 2 else juiz.split("/")[0]
    parentes = [m for m in painel if m.split("/")[1] == vendor]
    assert not parentes, f"the judge shares a vendor with {parentes}, so they are not two votes"
