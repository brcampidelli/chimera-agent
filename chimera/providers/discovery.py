"""Provider credentials nobody had to teach Chimera about.

LiteLLM is a hard dependency and speaks to 100+ providers. Chimera, until this module existed,
recognised exactly five: a key for Groq, Mistral, Together, xAI or any of the other ninety-odd was
answered with *"No provider key configured"* and the request never reached LiteLLM at all. The
product refused to work for a user holding a perfectly valid key — the worst failure a credential
gate can have, because from the outside it is indistinguishable from the product being broken.

**The rule.** A variable named ``<SOMETHING>_API_KEY`` with a non-empty value is a provider
credential, unless it is one of ours that is demonstrably not a source of models.

**Why a name pattern and not** ``litellm.provider_list``. Two reasons, both load-bearing:

1. ``has_any_key()`` is consulted at the top of dozens of CLI commands. ``provider_list`` is lazy but
   reading it imports LiteLLM, which this codebase defers deliberately in four separate places to
   keep CLI startup fast. Paying a heavy import on every command to improve an error message is a
   bad trade.
2. The obvious LiteLLM helper for this, ``validate_environment()``, is a three-hundred-line
   ``if/elif`` chain: a provider it has not heard of returns ``keys_in_environment=False`` with an
   empty ``missing_keys`` — a silent false negative, which is the exact bug being fixed here. The
   function that does the right thing, ``_infer_valid_provider_from_env_vars``, is private; the
   heuristic it implements is public convention, so this module implements the heuristic instead of
   importing the private name.

**The asymmetry that justifies accepting a false positive.** A stray ``FOO_API_KEY`` opening the gate
costs one clear ``BadRequestError`` from LiteLLM naming the unknown provider. A false negative costs
the entire product. Trading a late, legible error for an early, wrong refusal is the right way round.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping, Sequence
from functools import lru_cache

_API_KEY = re.compile(r"^[A-Z][A-Z0-9_]*_API_KEY$")

# The providers with a typed field, a rotating key pool and a labelled slot in the UI. Excluded here
# only to avoid reporting them twice — `configured_providers()` lists them first, from their fields.
FIRST_CLASS: frozenset[str] = frozenset(
    {
        "OPENROUTER_API_KEY",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "GEMINI_API_KEY",
        "DEEPSEEK_API_KEY",
    }
)

# Credentials Chimera already owns that match the pattern but do NOT serve models.
#
# ``STABILITY_API_KEY`` and ``ELEVENLABS_API_KEY`` are the ones that make this list mandatory rather
# than tidy: both are real entries in LiteLLM's own provider enum, so any discovery scheme — the name
# pattern here, or `provider_list` — would otherwise let someone whose only credential is a
# text-to-speech key pass the gate, and `chimera doctor` would print `elevenlabs` on the line listing
# who can serve a model. The rest are search and scraping keys, listed for the same reason.
NOT_A_MODEL_PROVIDER: frozenset[str] = frozenset(
    {
        "TAVILY_API_KEY",
        "BRAVE_API_KEY",
        "SERPAPI_API_KEY",
        "FIRECRAWL_API_KEY",
        "STABILITY_API_KEY",
        "ELEVENLABS_API_KEY",
    }
)


def provider_from_env_var(name: str) -> str | None:
    """``"GROQ_API_KEY"`` → ``"groq"``; ``None`` for anything that is not a generic provider key.

    The returned name is a label and a slug prefix, never a routing decision: LiteLLM resolves
    ``groq/llama-3.3-70b`` from the slug itself, and an unknown name simply finds no key pool and
    lets LiteLLM read the environment, which is what we want it to do.
    """
    if not _API_KEY.match(name):
        return None
    if name in FIRST_CLASS or name in NOT_A_MODEL_PROVIDER:
        return None
    return name.removesuffix("_API_KEY").lower()


def generic_providers(environ: Mapping[str, str] | None = None) -> list[str]:
    """Provider names discovered from the environment, sorted, without the first-class five.

    Takes the mapping rather than reading ``os.environ`` so the behaviour is testable without a
    process-wide side effect — which matters, because a test that has to mutate the real environment
    to check this is a test that can be broken by the machine it runs on.
    """
    env = os.environ if environ is None else environ
    found = {p for name, value in env.items() if value.strip() and (p := provider_from_env_var(name))}
    return sorted(found)


def env_file_credentials(
    env_file: str | os.PathLike[str] | Sequence[str | os.PathLike[str]] | None,
) -> dict[str, str]:
    """Provider keys that are in the ``.env`` but have no field on :class:`Settings`.

    This exists because of a gap that makes opening the gate useless on its own. ``Settings`` is the
    only reader of the ``.env``, and it is declared ``extra="ignore"`` — so a ``GROQ_API_KEY`` there
    is parsed, found to match no field, and **dropped**. It never becomes an attribute and never
    reaches ``os.environ``, so LiteLLM cannot see it either. And the ``.env`` is precisely the place
    the product tells people to put keys (``chimera init`` writes one). Without this function, a user
    following our own instructions would trade *"No provider key configured"* for a 401.

    ``env_file`` takes whatever ``SettingsConfigDict`` allows, which includes a *sequence* of files.
    They are read in order and a later file wins, matching pydantic-settings' own precedence — a
    detail worth honouring rather than assuming a single ``.env``, because getting it backwards would
    hand LiteLLM a key the rest of the configuration considers overridden.

    Returns the pairs; the caller decides what to do with them. Missing file, unreadable file or a
    missing ``python-dotenv`` all yield ``{}`` — configuration discovery must never be the reason a
    command cannot start.
    """
    if not env_file:
        return {}
    paths = (
        [env_file]
        if isinstance(env_file, (str, os.PathLike))
        else list(env_file)  # a sequence of files; later entries win
    )
    try:
        from dotenv import dotenv_values
    except ImportError:  # pragma: no cover - a hard dependency of pydantic-settings and litellm
        return {}
    found: dict[str, str] = {}
    for entry in paths:
        path = os.fspath(entry)
        if not os.path.isfile(path):
            continue
        try:
            raw = dotenv_values(path)
        except OSError:  # pragma: no cover - unreadable file
            continue
        found.update(
            {
                name: value
                for name, value in raw.items()
                if value and value.strip() and provider_from_env_var(name)
            }
        )
    return found


@lru_cache(maxsize=1)
def _litellm_providers() -> frozenset[str]:
    """LiteLLM's own provider names, or empty when LiteLLM cannot be imported.

    ``provider_list`` holds ``LlmProviders`` members, and the ``.value`` is not decoration: a
    ``str``-mixin enum keeps ``Enum.__hash__``, so a member does not match a plain string in a set,
    and ``str(member)`` renders ``"LlmProviders.GROQ"`` rather than ``"groq"``. Comparing the members
    directly made ``doctor`` report every discovered provider as unrecognised — including groq, which
    LiteLLM has supported all along.
    """
    try:
        import litellm

        return frozenset(str(getattr(p, "value", p)) for p in litellm.provider_list)
    except Exception:  # pragma: no cover - defensive: diagnostics must not crash
        return frozenset()


def litellm_known(names: list[str]) -> dict[str, bool]:
    """Which discovered names LiteLLM recognises — for ``doctor`` only, never for the gate.

    Importing LiteLLM costs real time, which is why the gate uses the name pattern instead. A
    diagnostic command is the one place where the precision is worth the second, and where an empty
    answer (LiteLLM unavailable) degrades to "unknown" rather than to a wrong refusal.
    """
    known = _litellm_providers()
    if not known:
        return {}
    return {name: name in known for name in names}
