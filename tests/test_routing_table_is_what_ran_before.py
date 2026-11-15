"""The routing table says what the code that consumes each role used before it existed, over a matrix.

Each row of the table is compared with what the CONSUMER ends up using, not with an intermediate
step: the tier ladder is ``resolve_tiers``; the escalation rungs are ``ladder_of``; the fusion cast is
``fusion_config``; the verified-answers model is ``escalation_model``; the decision model is what
``build_backend`` builds; and the code roles of a profile are ``roles.models.<role> or <the run's
model>``, with the reviewer going through ``review_model_for`` (which returns ``None`` when it would
be the editor, and then the run's own model reviews). Comparing with ``resolve(...).models.review``
alone looked right and was wrong under ``economy``.

The matrix walks 4 cost modes x 3 pin states x 3 key states x a fusion panel that is derived,
explicit or EMPTY x a policy that denies a provider or none x slugs with and without ``#effort``
(432 configurations) and pins the whole table in ``routing_snapshots/matrix.json``.

**Hermetic.** ``resolve_tiers`` reads the owner's policy file, the provider keys and every
``CHIMERA_*`` setting, and a machine that exports ``CHIMERA_OPENROUTER_KEYS`` or
``CHIMERA_WEAK_MODEL`` would otherwise move the "no keys" and "no pins" cases. Every variable Settings
can read is removed from the environment first (:func:`_clean_environment`), Settings is built with no
``.env``, and ``test_a_dirty_environment_does_not_move_the_snapshot`` exports the polluters and
checks that the answer is the committed one.

**Snapshot.** Unique values are written once and the cases refer to them by number, so the file is
small enough to review; the comparison rebuilds the full table from it and checks the rebuilt bytes
against the fresh ones as well as the file against a fresh encoding. To regenerate on purpose::

    ROUTING_SNAPSHOT_WRITE=1 pytest tests/test_routing_table_is_what_ran_before.py
"""

from __future__ import annotations

import itertools
import json
import os
from pathlib import Path
from typing import Any

import pytest

from chimera import config
from chimera.api.roles import resolve as resolve_roles
from chimera.api.roles import review_model_for
from chimera.config import Settings
from chimera.core.escalation import ladder_of
from chimera.decisions.factory import BACKENDS, build_backend, default_model_for
from chimera.fusion.factory import fusion_config
from chimera.fusion.verified import escalation_model
from chimera.providers.catalog import COST_MODES, resolve_tiers
from chimera.providers.routing import ROLE_IDS

SNAPSHOT = Path(__file__).parent / "routing_snapshots" / "matrix.json"
SNAPSHOT_LIMIT_BYTES = 200_000
PROFILES = ("economy", "balanced", "max")
CODE_ROLES = ("explore", "plan", "edit", "review")

PINS = {
    "none": {},
    "partial": {"CHIMERA_WEAK_MODEL": "anthropic/pin-weak"},
    "total": {
        "CHIMERA_WEAK_MODEL": "anthropic/pin-weak",
        "CHIMERA_MID_MODEL": "openrouter/pin/mid",
        "CHIMERA_ORCHESTRATOR_MODEL": "openrouter/pin/top",
    },
}
KEYS = {
    "none": {},
    "openrouter": {"OPENROUTER_API_KEY": "k"},
    "other": {"ANTHROPIC_API_KEY": "k"},
}
PANEL = {
    "derived": {},
    "explicit": {"CHIMERA_FUSION_PANEL": "openrouter/p/one,openrouter/p/two#high"},
    "empty": {"CHIMERA_FUSION_PANEL": ""},
}
POLICY = {
    "none": None,
    "deny_anthropic": {"rules": [{"action": "provider.use", "resource": "anthropic", "effect": "deny"}]},
}
EFFORT = {"plain": "", "effort": "#high"}

PARTS = ("tiers", "rungs", "fusion", "verify_escalation", "decisions", "profiles", "lists")


def _setting_names() -> set[str]:
    """Every name the environment could use to set a field: its alias(es) and the field name."""
    names: set[str] = set()
    for field_name, info in Settings.model_fields.items():
        names.add(field_name.upper())
        alias = info.validation_alias
        if isinstance(alias, str):
            names.add(alias.upper())
        else:
            choices = getattr(alias, "choices", None) or ()
            names.update(str(choice).upper() for choice in choices if isinstance(choice, str))
    return names


def _clean_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove every variable Settings (or the key pools) could read, so no machine moves the answer."""
    names = _setting_names()
    for name in list(os.environ):
        upper = name.upper()
        if upper.startswith("CHIMERA_") or upper in names or upper.endswith(("_API_KEY", "_KEYS")):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr("chimera.providers.discovery.generic_providers", lambda environ=None: [])
    monkeypatch.setattr(config, "_STARTUP_ENV", frozenset())


@pytest.fixture(autouse=True)
def _the_machine_does_not_matter(monkeypatch: pytest.MonkeyPatch) -> None:
    _clean_environment(monkeypatch)


def _name(parts: tuple[str, ...]) -> str:
    return "|".join(parts)


def _settings(parts: tuple[str, ...], workdir: Path) -> Settings:
    mode, pins, keys, panel, policy, effort = parts
    suffix = EFFORT[effort]
    kwargs: dict[str, Any] = {
        "CHIMERA_COST_MODE": mode, "CHIMERA_DEFAULT_MODEL": f"openrouter/test/dflt{suffix}",
    }
    kwargs.update({k: v + suffix for k, v in PINS[pins].items()})
    kwargs.update(KEYS[keys])
    kwargs.update(PANEL[panel])
    document = POLICY[policy]
    if document is not None:
        path = workdir / f"{policy}.json"
        path.write_text(json.dumps(document), encoding="utf-8")
        kwargs["CHIMERA_POLICY_FILE"] = str(path)
    return Settings(_env_file=None, CHIMERA_HOME=str(workdir), **kwargs)  # type: ignore[call-arg]


def _legacy(settings: Settings) -> dict[str, Any]:
    """What the code that decides today answers, as plain values."""
    tiers = resolve_tiers(settings)
    cast = fusion_config(settings)
    run_model = settings.default_model
    profiles: dict[str, dict[str, str]] = {}
    for profile in PROFILES:
        plan = resolve_roles(profile, settings)  # type: ignore[arg-type]
        profiles[profile] = {
            "explore": plan.models.explore or run_model,
            "plan": plan.models.plan or run_model,
            "edit": plan.models.edit or run_model,
            "review": review_model_for(plan) or run_model,
        }
    return {
        "tiers": [tiers.weak, tiers.mid, tiers.top, tiers.entry, tiers.source],
        "rungs": ladder_of(settings),
        "fusion": [cast.panel, cast.judge, cast.synthesizer],
        "verify_escalation": escalation_model(settings),
        "decisions": default_model_for(settings, "local_logprob"),
        "profiles": profiles,
    }


def _table_values(settings: Settings) -> dict[str, Any]:
    table = settings.routing_table()
    tiers = table.tiers()
    return {
        "tiers": [tiers.weak, tiers.mid, tiers.top, tiers.entry, tiers.source],
        "rungs": table.escalation_rungs(),
        "fusion": [
            [r.slug for r in table.lists["fusion.panel"]],
            table.route("fusion.judge").slug,
            table.route("fusion.synthesizer").slug,
        ],
        "verify_escalation": table.route("verify_escalation").slug,
        "decisions": table.route("decisions").slug,
        "profiles": {
            profile: {name: table.profile_routes(profile)[f"code.{name}"].slug for name in CODE_ROLES}
            for profile in PROFILES
        },
        "routes": {
            role: [r.slug, r.origin, r.effort_origin, r.inherits] for role, r in table.routes.items()
        },
        "lists": {role: [r.slug for r in members] for role, members in table.lists.items()},
    }


def _attempt(call: Any) -> Any:
    try:
        return call()
    except Exception as exc:  # the matrix may include a policy that leaves nothing permitted
        return f"raises:{type(exc).__name__}"


def _cases() -> list[tuple[str, ...]]:
    return list(itertools.product(COST_MODES, PINS, KEYS, PANEL, POLICY, EFFORT))


def _render(cases: list[tuple[str, ...]], workdir: Path) -> tuple[dict[str, Any], list[str]]:
    """The table for each case, and every disagreement with the legacy answer."""
    answers: dict[str, Any] = {}
    mismatches: list[str] = []
    for parts in cases:
        settings = _settings(parts, workdir)
        legacy = _attempt(lambda s=settings: _legacy(s))  # type: ignore[misc]
        table = _attempt(lambda s=settings: _table_values(s))  # type: ignore[misc]
        answers[_name(parts)] = table
        if isinstance(legacy, str) or isinstance(table, str):
            if legacy != table:
                mismatches.append(f"{_name(parts)}: legacy {legacy!r} vs table {table!r}")
            continue
        for key in (*[p for p in PARTS if p in legacy], "profiles"):
            if legacy[key] != table[key]:
                mismatches.append(f"{_name(parts)}: {key}: legacy {legacy[key]!r} vs table {table[key]!r}")
    return answers, mismatches


# --- the compact snapshot -----------------------------------------------------------------------


def _encode(answers: dict[str, Any]) -> bytes:
    """Unique values once, each case as a list of numbers; see the module docstring."""
    pools: dict[str, list[Any]] = {name: [] for name in (*PARTS, "record", "routes")}
    seen: dict[str, dict[str, int]] = {name: {} for name in pools}

    def intern(kind: str, value: Any) -> int:
        key = json.dumps(value, sort_keys=True, ensure_ascii=False)
        if key not in seen[kind]:
            seen[kind][key] = len(pools[kind])
            pools[kind].append(value)
        return seen[kind][key]

    cases: dict[str, list[int]] = {}
    refusals: dict[str, str] = {}
    for name, table in answers.items():
        if isinstance(table, str):
            refusals[name] = table
            continue
        entry = [intern(part, table[part]) for part in PARTS]
        vector = [intern("record", table["routes"][role]) for role in ROLE_IDS]
        cases[name] = [*entry, intern("routes", vector)]
    document = {
        "roles": list(ROLE_IDS), "order": [*PARTS, "routes"], "pools": pools,
        "cases": cases, "refusals": refusals,
    }
    return (json.dumps(document, sort_keys=True, indent=0, ensure_ascii=False) + "\n").encode("utf-8")


def _decode(raw: bytes) -> dict[str, Any]:
    document = json.loads(raw.decode("utf-8"))
    pools, roles = document["pools"], document["roles"]
    answers: dict[str, Any] = dict(document["refusals"])
    for name, entry in document["cases"].items():
        table = {part: pools[part][i] for part, i in zip(PARTS, entry, strict=False)}
        vector = pools["routes"][entry[len(PARTS)]]
        table["routes"] = {role: pools["record"][i] for role, i in zip(roles, vector, strict=True)}
        answers[name] = table
    return answers


def _canonical(answers: dict[str, Any]) -> bytes:
    return json.dumps(answers, sort_keys=True, ensure_ascii=False).encode("utf-8")


def test_the_table_is_what_the_consumers_used(tmp_path: Path) -> None:
    answers, mismatches = _render(_cases(), tmp_path)
    assert not mismatches, "\n".join(mismatches[:20])
    encoded = _encode(answers)
    if os.environ.get("ROUTING_SNAPSHOT_WRITE") == "1":
        SNAPSHOT.parent.mkdir(exist_ok=True)
        SNAPSHOT.write_bytes(encoded)
    committed = SNAPSHOT.read_bytes()
    assert committed == encoded, (
        "the routing table changed what it answers; if on purpose, regenerate with "
        "ROUTING_SNAPSHOT_WRITE=1 and say why in the commit"
    )
    # The reconstruction is byte-for-byte the fresh table, so the dedup lost nothing.
    assert _canonical(_decode(committed)) == _canonical(answers)
    assert len(committed) < SNAPSHOT_LIMIT_BYTES, f"{len(committed)} bytes: the snapshot should stay reviewable"


def test_a_dirty_environment_does_not_move_the_snapshot(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The cases most exposed to the machine: no keys and no pins. Export what a real machine might.
    for name, value in {
        "CHIMERA_OPENROUTER_KEYS": "k", "CHIMERA_WEAK_MODEL": "x/y", "CHIMERA_MID_MODEL": "x/y",
        "CHIMERA_FUSION_PANEL": "x/y", "CHIMERA_COST_MODE": "premium", "OPENROUTER_API_KEY": "k",
        "ANTHROPIC_API_KEY": "k", "CHIMERA_DECISION_MODEL": "x/y", "CHIMERA_REVIEW_MODEL": "x/y",
        "CHIMERA_VERIFIED_ANSWERS_ESCALATE_MODEL": "x/y", "GROQ_API_KEY": "k",
    }.items():
        monkeypatch.setenv(name, value)
    _clean_environment(monkeypatch)
    assert not [n for n in os.environ if n.upper().startswith("CHIMERA_") or n.upper().endswith("_API_KEY")]
    exposed = [c for c in _cases() if c[1] == "none" and c[2] == "none"]
    answers, mismatches = _render(exposed, tmp_path)
    assert not mismatches, "\n".join(mismatches[:10])
    committed = _decode(SNAPSHOT.read_bytes())
    assert exposed
    assert {name: committed[name] for name in answers} == answers


def test_the_matrix_has_every_dimension_and_every_role() -> None:
    cases = _cases()
    assert len(cases) == 4 * 3 * 3 * 3 * 2 * 2
    assert len(set(cases)) == len(cases)
    answered = _decode(SNAPSHOT.read_bytes())
    assert set(answered) == {_name(c) for c in cases}
    for table in answered.values():
        assert set(table["routes"]) == set(ROLE_IDS)
    empty = answered[_name(("auto", "none", "none", "empty", "none", "plain"))]
    assert empty["fusion"][0] == [] and empty["lists"]["fusion.panel"] == []


def test_a_policy_that_leaves_nothing_permitted_refuses_the_same_way(tmp_path: Path) -> None:
    # The matrix denies one provider and always leaves another; this denies all, so the table and the
    # resolver must both refuse rather than the table answering with a denied slug. It is the ONE
    # exception the table lets through.
    path = tmp_path / "deny_all.json"
    path.write_text(
        json.dumps({"rules": [{"action": "provider.use", "resource": "*", "effect": "deny"}]}),
        encoding="utf-8",
    )
    settings = Settings(_env_file=None, CHIMERA_POLICY_FILE=str(path), CHIMERA_HOME=str(tmp_path))  # type: ignore[call-arg]
    assert _attempt(lambda: resolve_tiers(settings)) == _attempt(lambda: settings.routing_table())
    assert _attempt(lambda: settings.routing_table()) == "raises:ProviderDeniedError"


@pytest.mark.parametrize("backend", BACKENDS)
@pytest.mark.parametrize("model", ["", "custom/model"])
def test_the_decision_role_follows_default_model_for(backend: str, model: str) -> None:
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None, CHIMERA_DECISION_BACKEND=backend, CHIMERA_DECISION_MODEL=model
    )
    route = settings.routing_table().route("decisions")
    assert route.slug == (model or default_model_for(settings, backend))


@pytest.mark.parametrize("backend", ["local_logprob", "hosted_verbalized"])
@pytest.mark.parametrize("model", ["", "custom/model"])
def test_the_decision_role_is_the_model_the_backend_is_built_with(backend: str, model: str) -> None:
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None, CHIMERA_DECISION_BACKEND=backend, CHIMERA_DECISION_MODEL=model
    )
    built = build_backend(settings, gateway=object())
    assert settings.routing_table().route("decisions").slug == built.model  # type: ignore[attr-defined]


def test_every_simple_field_role_reads_its_setting() -> None:
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None,
        CHIMERA_IMAGE_MODEL="img/m", CHIMERA_TTS_MODEL="tts/m", CHIMERA_AUDIO_MODEL="aud/m",
        CHIMERA_VIDEO_MODEL="vid/m", CHIMERA_EMBED_MODEL="emb/m", CHIMERA_COMPLETE_MODEL="cmp:1b",
        CHIMERA_IMAGE_MODEL_LOCAL="loc/m", CHIMERA_CHAT_VOICE_MODEL="/v/voice.onnx",
        CHIMERA_VOICE_MODEL="voice/m#low", CHIMERA_VOICE_WORK_MODEL="work/m",
    )
    table = settings.routing_table()
    roles = ("image", "tts", "audio", "video", "embed", "complete", "image_local", "chat_voice", "voice", "voice_work")
    assert {role: table.route(role).slug for role in roles} == {
        "image": settings.image_model, "tts": settings.tts_model, "audio": settings.audio_model,
        "video": settings.video_model, "embed": settings.embed_model, "complete": settings.complete_model,
        "image_local": settings.image_model_local, "chat_voice": settings.chat_voice_model,
        "voice": settings.voice_model, "voice_work": settings.voice_work_model,
    }
    assert table.route("voice").effort == "low"
