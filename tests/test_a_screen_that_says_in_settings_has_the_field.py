"""A sentence that sends someone to Settings has to find a field there.

The Tools screen told every tool that needs a credential "Needs {vars} — add it in Settings". For the
Tavily key that is true. For ``send_email``, ``read_email`` and ``calendar_events`` it was false: the
SMTP, IMAP and ICS variables are not in ``ALLOWED_KEYS``, so Settings has no field for them and
``PATCH /api/config`` refuses them. Nothing errors when a sentence promises a field — the user looks,
does not find it, and concludes the feature is broken or that they are.

The fix is the sentence, not the allowlist. Saving SMTP credentials from the app is what arms
``send_email``; that is the owner's call to make on its own, not a side effect of a copy fix. So the
rows whose variables Settings cannot save carry ``in_settings: False`` and name the ``.env``.

Held here, for every "in Settings" in the app's English dictionary: the string is mapped to the
setting it is talking about, that setting is one ``patch_config`` will write, and a screen renders a
control for it. A new "in Settings" sentence fails until someone answers which field it means — or
rewrites the claim.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from chimera.api.config_api import ALLOWED_KEYS, is_editable, read_config
from chimera.config import Settings
from chimera.tools.conditional import CONDITIONAL_TOOLS, unavailable

DESKTOP = Path(__file__).resolve().parents[1] / "apps" / "desktop" / "src"

#: Each English string that tells the reader something is in Settings, and the setting it means.
#: ``tools.unavailable.set`` is the one whose variables arrive at runtime; it is held by the tests
#: on ``in_settings`` below rather than by a fixed name here.
CLAIMS: dict[str, tuple[str, ...]] = {
    "tools.unavailable.set": (),
    "governance.kernelOff": ("CHIMERA_GOVERNANCE",),
    "settings.voice.pickBlurb": ("CHIMERA_VOICE_MODEL", "CHIMERA_VOICE_WORK_MODEL"),
    "memory.layers.empty": ("CHIMERA_CHAT_MEMORY",),
    "code.posture.unguarded": ("CHIMERA_GUARD_CHAT",),
    "mcp.autoloadOff": ("CHIMERA_MCP_AUTOLOAD",),
    "mcp.note": ("CHIMERA_GUARD_CHAT",),
    "governance.privacy.retentionAllow": (
        "CHIMERA_OPENROUTER_DATA_COLLECTION",
        "CHIMERA_OPENROUTER_ZDR",
    ),
}

_SETTINGS_CLAIM = re.compile(r"\bin Settings\b")


def _english() -> dict[str, str]:
    """The ``en`` dictionary of ``i18n.tsx``, key -> value, read from the source.

    Values are written either on the key's line or on the next one, double- or single-quoted.
    """
    text = (DESKTOP / "lib" / "i18n.tsx").read_text(encoding="utf-8")
    block = text[text.index("const en: Dict = {") : text.index("const pt: Dict = {")]
    entry = re.compile(
        r'^\s*"([\w.]+)":\s*(?:"((?:[^"\\]|\\.)*)"|\'((?:[^\'\\]|\\.)*)\')',
        re.M,
    )
    return {m.group(1): m.group(2) if m.group(2) is not None else m.group(3) for m in entry.finditer(block)}


def _rendered_by_a_screen(env: str) -> bool:
    """Whether some component (not a test) names the variable — the control that writes it."""
    for path in (DESKTOP / "components").rglob("*.tsx"):
        if ".test." in path.name:
            continue
        if env in path.read_text(encoding="utf-8"):
            return True
    return False


def test_the_dictionary_reader_sees_the_dictionary() -> None:
    # An empty parse would let every check below pass with nothing checked.
    english = _english()
    assert len(english) > 500
    assert english["tools.unavailable.set"] == "Needs {vars} — add it in Settings."


def test_every_in_settings_sentence_names_a_setting_the_app_can_save() -> None:
    claims = {key for key, value in _english().items() if _SETTINGS_CLAIM.search(value)}

    unmapped = claims - set(CLAIMS)
    assert not unmapped, (
        f"these strings send the reader to Settings and nobody said which field: {sorted(unmapped)} "
        "— map each to the env var it means, or rewrite the claim"
    )
    stale = set(CLAIMS) - claims
    assert not stale, f"mapped here but no longer saying 'in Settings': {sorted(stale)}"

    for key, envs in CLAIMS.items():
        for env in envs:
            assert env in ALLOWED_KEYS, f"{key} says {env} is in Settings; PATCH /api/config refuses it"
            assert _rendered_by_a_screen(env), f"{key} says {env} is in Settings; no screen renders it"


def test_a_key_row_claims_settings_exactly_when_settings_can_save_every_variable() -> None:
    rows = {row["name"]: row for row in unavailable(set())}
    settings_fields = {p["env"] for p in read_config(Settings())["providers"]}

    for tool in CONDITIONAL_TOOLS:
        if tool.kind != "key":
            continue
        row = rows[tool.name]
        editable = all(is_editable(v) for v in tool.variables)
        assert row["in_settings"] is editable, tool.name
        if row["in_settings"]:
            # Editable is the endpoint's half; a field on the credentials list is the screen's.
            assert set(tool.variables) <= settings_fields, f"{tool.name}: no field for {tool.variables}"


@pytest.mark.parametrize("name", ["send_email", "read_email", "calendar_events"])
def test_mail_and_calendar_are_set_in_the_env_file_not_in_settings(name: str) -> None:
    """The three rows the old sentence lied about — and the allowlist deliberately left alone."""
    row = next(r for r in unavailable(set()) if r["name"] == name)

    assert row["in_settings"] is False
    for variable in row["variables"]:
        assert isinstance(variable, str)
        assert variable not in ALLOWED_KEYS, f"{variable} became editable — that arms {name}; decide on purpose"


def test_the_screen_has_a_sentence_for_the_env_file() -> None:
    english = _english()
    assert ".env" in english["tools.unavailable.setInEnv"]
    assert not _SETTINGS_CLAIM.search(english["tools.unavailable.setInEnv"])
    tools = (DESKTOP / "components" / "Tools.tsx").read_text(encoding="utf-8")
    assert "tools.unavailable.setInEnv" in tools and "in_settings" in tools


def test_the_scheduled_jobs_switch_says_the_app_has_to_be_open() -> None:
    """The same family on the Automation row: a switch that read like a scheduler.

    `CHIMERA_APP_CRON` starts the cron daemon inside `chimera app`, so jobs fire only while the app
    runs; 24/7 is `chimera serve --cron`. The hint said "the daemon that fires the jobs, inside this
    app" and left the reader to discover the rest at 7am. Every language has to name the command,
    since it is the one part a translator must not paraphrase.
    """
    text = (DESKTOP / "lib" / "i18n.tsx").read_text(encoding="utf-8")
    hints = re.findall(r'"settings\.hint\.appCron":\s*"([^"]*)"', text)

    assert len(hints) == 10, f"expected the hint in all ten languages, found {len(hints)}"
    for hint in hints:
        assert "`chimera serve --cron`" in hint, hint
    assert "only while this app is open" in _english()["settings.hint.appCron"]
