"""Which model reviews the change: one from a different family than the model that wrote it.

`chimera.api.roles.review_model_for` already refuses to let the reviewer be the *same model* as the
editor. That is the weaker half of the rule. Two models from one vendor share training data, tuning
and taste, so a sibling agrees with the author for the same reasons the author was wrong, and
self-preference in LLM judges is one of the two biases that replicate (study 25 §2.9). Different
model, same family, is still grading your own homework.

A slug names a route, not a family: ``openrouter/deepseek/deepseek-v4-flash`` and
``deepseek/deepseek-chat`` are one family behind two providers. :func:`model_family` reads the
vendor segment where there is one and the model name where there is not.

Which model of another family is a separate question, and it was measured rather than inherited:
see :data:`MEASURED_REVIEWERS`.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

#: Routers and runtimes: a first segment that says where a model is served, not who made it.
_ROUTERS = frozenset({
    "openrouter", "together_ai", "together", "fireworks_ai", "groq", "deepinfra", "bedrock",
    "azure", "azure_ai", "ollama", "ollama_chat", "lm_studio", "vllm", "hosted_vllm", "huggingface",
    "replicate", "nebius", "novita", "vertex_ai", "sambanova", "cerebras",
})

#: Vendor segments, and the name each family goes by here.
_VENDORS = {
    "openai": "openai", "anthropic": "anthropic", "google": "google", "gemini": "google",
    "deepseek": "deepseek", "z-ai": "zhipu", "zai": "zhipu", "zhipuai": "zhipu", "thudm": "zhipu",
    "meta-llama": "meta", "meta": "meta", "mistralai": "mistral", "mistral": "mistral",
    "moonshotai": "moonshot", "qwen": "qwen", "alibaba": "qwen", "x-ai": "xai", "xai": "xai",
    "microsoft": "microsoft", "cohere": "cohere", "nvidia": "nvidia", "amazon": "amazon",
}

#: Model-name prefixes, for a slug with no vendor segment (a local ``ollama/qwen3:4b``).
_NAME_PREFIXES = (
    ("gpt", "openai"), ("o1", "openai"), ("o3", "openai"), ("o4", "openai"),
    ("claude", "anthropic"), ("gemini", "google"), ("gemma", "google"), ("deepseek", "deepseek"),
    ("glm", "zhipu"), ("llama", "meta"), ("codestral", "mistral"), ("devstral", "mistral"),
    ("ministral", "mistral"), ("mixtral", "mistral"), ("mistral", "mistral"), ("qwen", "qwen"),
    ("qwq", "qwen"), ("kimi", "moonshot"), ("grok", "xai"), ("phi", "microsoft"),
    ("command", "cohere"), ("nemotron", "nvidia"), ("nova", "amazon"),
)


def model_family(slug: str) -> str:
    """The family a model belongs to, e.g. ``"deepseek"``; the bare name when nothing matches."""
    parts = [p for p in slug.strip().lower().split("/") if p]
    while len(parts) > 1 and parts[0] in _ROUTERS:
        parts = parts[1:]
    if not parts:
        return ""
    if len(parts) > 1 and parts[0] in _VENDORS:
        return _VENDORS[parts[0]]
    name = parts[-1]
    for prefix, family in _NAME_PREFIXES:
        if name.startswith(prefix):
            return family
    return parts[0] if len(parts) > 1 else name.split(":", 1)[0]


#: Models measured as reviewers, in the order `chimera review` prefers them by default.
#:
#: The command used to read the tier ladder first, strongest rung first, on the reasoning that the
#: role placed on the top rung should review. As a reviewer that rung ran away: glm-5.3 reasoned to
#: the 32,000-token ceiling and came back empty on 4 of 18 reviews, at 44 times the default model's
#: price, for no recall the set could show (`bench/review_seeded`). `bench/review_reviewer`
#: (2026-09-26) then ran three cheaper models from other families on the same seeded diffs and
#: chose by a rule frozen before the run: the cheapest whose recall is within 10 points of the
#: default model's, with at most 5% of reviews incomplete and at most two more findings than the
#: default model's on ten clean diffs. gpt-6-luna qualified (39 of 40 seeded reviews showed the
#: defect, none incomplete, at the default model's own cost per review); qwen3.7-flash did not, and
#: mistral-small-3.2 could not be measured through the one route that serves the product's request.
#:
#: Two families, so every author has an entry from outside its own: the default model, measured as
#: the reference, reviews what gpt-6-luna wrote. A list rather than a rung, because only a list says
#: that its entries were measured at this job. Keys that call neither fall through to the ladder.
MEASURED_REVIEWERS: tuple[str, ...] = (
    "openrouter/openai/gpt-6-luna",
    "openrouter/deepseek/deepseek-v4-flash-0731",
)


@dataclass(frozen=True)
class ReviewerChoice:
    """The reviewer, the author it was chosen against, and where the choice came from."""

    model: str
    author_model: str
    # "flag" | "setting" | "measured" | "tier ladder" | "fusion panel" | "catalogue" | "fallback"
    source: str

    @property
    def family(self) -> str:
        return model_family(self.model)

    @property
    def author_family(self) -> str:
        return model_family(self.author_model)

    @property
    def same_family(self) -> bool:
        return self.family == self.author_family


def choose_reviewer(
    author_model: str,
    *,
    explicit: str = "",
    setting: str = "",
    measured: Iterable[str] = (),
    ladder: Iterable[str] = (),
    panel: Iterable[str] = (),
    catalogue: Iterable[str] = (),
    reachable: Iterable[str] | None = None,
) -> ReviewerChoice:
    """The first candidate from another family than ``author_model``.

    An explicit choice (the flag, then the setting) is honoured as given, even from the author's
    family: silently replacing a model someone named is the kind of rerouting
    `chimera.providers.catalog.resolve_tiers` refuses to do, and :attr:`ReviewerChoice.same_family`
    lets the report say what was lost. Otherwise the models measured as reviewers come first
    (:data:`MEASURED_REVIEWERS`, in their order), then the tier ladder strongest first, then the
    fusion panel, then the catalogue, keeping only models the configured keys can call when
    ``reachable`` is given.

    When nothing from another family can be called, the author's own model reviews, and the report
    says so; an absent review would be worse than a weaker one.
    """
    if explicit:
        return ReviewerChoice(explicit, author_model, "flag")
    if setting:
        return ReviewerChoice(setting, author_model, "setting")
    author = model_family(author_model)
    allowed = set(reachable) if reachable is not None else None
    sources = (
        ("measured", measured), ("tier ladder", ladder), ("fusion panel", panel),
        ("catalogue", catalogue),
    )
    for source, models in sources:
        for model in models:
            if not model or model_family(model) == author:
                continue
            if allowed is not None and model not in allowed:
                continue
            return ReviewerChoice(model, author_model, source)
    return ReviewerChoice(author_model, author_model, "fallback")
