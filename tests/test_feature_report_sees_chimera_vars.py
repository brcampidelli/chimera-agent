"""`chimera features` told people to set variables they had already set.

`feature_status` asked `settings.credentials()` whether a variable was configured. That dict holds
thirteen slots, every one of them an unprefixed provider key — `OPENAI_API_KEY`, `TAVILY_API_KEY`,
and so on. Four features declare `CHIMERA_*` names instead: `CHIMERA_IMAP_HOST`,
`CHIMERA_SMTP_HOST`, `CHIMERA_CALENDAR_ICS_URL`, `CHIMERA_OPENAI_KEYS`. A `dict.get` for a key that
cannot be in the dict is `None` every time.

Measured: with `CHIMERA_IMAP_HOST` exported and `Settings` reading it correctly, the report says
`has_key=False` and prints *"set CHIMERA_IMAP_HOST"* in yellow — while `default_registry()` in the
same process registers `read_email`. **The tool works and the report says it cannot.** That is worse
than a missing feature: it sends someone to debug configuration that was already right.

The fix resolves a variable name against the model's own field aliases rather than against a second
hand-written table, because a second hand-written table is exactly what drifted.
"""

from __future__ import annotations

import pytest

from chimera.config import Settings
from chimera.features import CATALOG, feature_status


def _status(settings: Settings, name: str):
    return next(s for s in feature_status(settings) if s.feature.name == name)


@pytest.mark.parametrize(
    ("feature", "env", "value"),
    [
        ("read_email", "CHIMERA_IMAP_HOST", "imap.example.com"),
        ("email", "CHIMERA_SMTP_HOST", "smtp.example.com"),
        ("calendar", "CHIMERA_CALENDAR_ICS_URL", "https://example.com/cal.ics"),
    ],
)
def test_a_chimera_variable_that_is_set_is_reported_as_set(
    monkeypatch: pytest.MonkeyPatch, feature: str, env: str, value: str
) -> None:
    monkeypatch.setenv(env, value)
    settings = Settings()

    assert _status(settings, feature).has_key is True


def test_it_is_still_false_when_nothing_is_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    # The other direction, and the one that keeps the fix from being "always true": a report that
    # said yes to everything would pass every assertion above and be useless.
    for var in ("CHIMERA_IMAP_HOST", "CHIMERA_SMTP_HOST", "CHIMERA_CALENDAR_ICS_URL"):
        monkeypatch.delenv(var, raising=False)
    settings = Settings()

    assert _status(settings, "read_email").has_key is False
    assert _status(settings, "email").has_key is False
    assert _status(settings, "calendar").has_key is False


def test_the_unprefixed_provider_keys_still_work(monkeypatch: pytest.MonkeyPatch) -> None:
    # The path that was never broken. Without this, a fix that only looked at model fields would
    # break the twelve features that read a plain provider key.
    monkeypatch.setenv("TAVILY_API_KEY", "sk-whatever")
    settings = Settings()

    assert _status(settings, "web_search").has_key is True


def test_every_name_a_feature_asks_for_is_a_name_something_can_answer() -> None:
    """The class, not the four instances.

    A feature naming a variable that no lookup can resolve is silently always-false, which is the
    defect and not a spelling mistake. Checked against both sources at once so a fifth feature
    added with a `CHIMERA_*` name — or with a typo in a provider key — fails here rather than in a
    user's terminal.
    """
    settings = Settings()
    known = set(settings.credentials())
    aliases = {
        field.validation_alias
        for field in type(settings).model_fields.values()
        if isinstance(field.validation_alias, str)
    }

    unanswerable = [
        (feature.name, var)
        for feature in CATALOG
        for var in feature.env_any
        if var not in known and var not in aliases
    ]

    assert unanswerable == []
