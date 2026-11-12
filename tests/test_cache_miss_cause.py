"""Why a Code turn did not read the prompt cache, classified from facts.

Each cause is a rule over numbers the process already holds. These tests feed the rules synthetic
facts, so every branch is exercised on its own and an order change between two rules shows up here.
"""

from __future__ import annotations

from chimera.core.cache_miss import (
    CAUSES,
    CacheFacts,
    classify_cache_miss,
    external_turn_record,
    facts_for_turn,
    turn_record,
)
from chimera.core.steplog import StepLog, StepRecord
from chimera.providers.cache_ttl import (
    ANTHROPIC_EPHEMERAL_TTL_SECONDS,
    cache_ttl_seconds,
    explicit_cache_route,
)

ANTHROPIC = "anthropic/claude-haiku-5.5"
DEEPSEEK = "deepseek/deepseek-chat"


def _facts(
    *,
    cache_read_tokens: int | None = 0,
    first_turn: bool = False,
    explicit_cache: bool = True,
    ttl_seconds: float | None = ANTHROPIC_EPHEMERAL_TTL_SECONDS,
    gap_seconds: float | None = 10.0,
    model_changed: bool | None = False,
    route_changed: bool | None = False,
    system_changed: bool | None = False,
    prefix_changed: bool | None = False,
    tools_changed: bool | None = False,
) -> CacheFacts:
    return CacheFacts(
        cache_read_tokens=cache_read_tokens,
        first_turn=first_turn,
        explicit_cache=explicit_cache,
        ttl_seconds=ttl_seconds,
        gap_seconds=gap_seconds,
        model_changed=model_changed,
        route_changed=route_changed,
        system_changed=system_changed,
        prefix_changed=prefix_changed,
        tools_changed=tools_changed,
    )


def test_a_cache_read_is_none_whatever_else_happened() -> None:
    facts = _facts(cache_read_tokens=5000, first_turn=True, gap_seconds=9999.0, model_changed=True)
    assert classify_cache_miss(facts) == "none"


def test_a_silent_provider_has_no_miss_to_explain() -> None:
    assert classify_cache_miss(_facts(cache_read_tokens=None)) == "unknown"


def test_the_first_turn_of_a_conversation_is_a_first_turn_not_an_expiry() -> None:
    facts = _facts(first_turn=True, gap_seconds=None)
    assert classify_cache_miss(facts) == "first_turn"


def test_a_changed_model_or_route_is_named_before_anything_else() -> None:
    assert classify_cache_miss(_facts(model_changed=True, gap_seconds=9999.0)) == "model_or_route_changed"
    assert classify_cache_miss(_facts(route_changed=True)) == "model_or_route_changed"


def test_a_route_without_explicit_cache_says_so_rather_than_blaming_the_prompt() -> None:
    facts = _facts(explicit_cache=False, ttl_seconds=None)
    assert classify_cache_miss(facts) == "provider_no_cache"


def test_a_route_without_explicit_cache_still_names_a_real_change_of_the_prompt() -> None:
    # Its cache is automatic, and an automatic cache misses when the prefix moved: saying the
    # provider does not cache would hide the cause that is known.
    base = {"explicit_cache": False, "ttl_seconds": None}
    assert classify_cache_miss(_facts(system_changed=True, **base)) == "prefix_changed"
    assert classify_cache_miss(_facts(tools_changed=True, **base)) == "tools_changed"
    assert classify_cache_miss(_facts(prefix_changed=True, **base)) == "prefix_changed"


def test_an_expiry_beyond_the_known_lifetime_is_ttl_expired_even_if_the_prompt_changed() -> None:
    facts = _facts(gap_seconds=ANTHROPIC_EPHEMERAL_TTL_SECONDS + 1, prefix_changed=True)
    assert classify_cache_miss(facts) == "ttl_expired"


def test_a_gap_within_the_lifetime_is_not_an_expiry() -> None:
    facts = _facts(gap_seconds=ANTHROPIC_EPHEMERAL_TTL_SECONDS, prefix_changed=True)
    assert classify_cache_miss(facts) == "prefix_changed"


def test_an_unknown_lifetime_never_produces_ttl_expired() -> None:
    facts = _facts(ttl_seconds=None, gap_seconds=10_000.0)
    assert classify_cache_miss(facts) == "unknown"


def test_a_changed_system_prompt_or_prefix_is_prefix_changed() -> None:
    assert classify_cache_miss(_facts(system_changed=True)) == "prefix_changed"
    assert classify_cache_miss(_facts(prefix_changed=True)) == "prefix_changed"


def test_changed_tools_are_tools_changed_when_the_prefix_held() -> None:
    assert classify_cache_miss(_facts(tools_changed=True)) == "tools_changed"


def test_tools_changed_is_named_when_the_combined_prefix_moved_with_them() -> None:
    # The prefix fingerprint covers the tool schemas, so it moves whenever the tools do.
    assert classify_cache_miss(_facts(tools_changed=True, prefix_changed=True)) == "tools_changed"
    # When the system moved too, the system is named: it is the earlier part of the prefix.
    both = _facts(system_changed=True, tools_changed=True, prefix_changed=True)
    assert classify_cache_miss(both) == "prefix_changed"


def test_a_miss_no_rule_explains_is_unknown_not_a_guess() -> None:
    assert classify_cache_miss(_facts()) == "unknown"


def test_every_returned_cause_is_in_the_fixed_list() -> None:
    cases = [
        _facts(cache_read_tokens=None),
        _facts(cache_read_tokens=3),
        _facts(first_turn=True),
        _facts(model_changed=True),
        _facts(explicit_cache=False),
        _facts(),
        _facts(prefix_changed=True),
        _facts(tools_changed=True),
    ]
    assert {classify_cache_miss(c) for c in cases} <= set(CAUSES)


def test_the_lifetime_table_documents_anthropic_and_leaves_the_rest_unknown() -> None:
    assert ANTHROPIC_EPHEMERAL_TTL_SECONDS == 300.0
    assert cache_ttl_seconds(ANTHROPIC) == 300.0
    assert cache_ttl_seconds("openrouter/anthropic/claude-sonnet") == 300.0
    assert cache_ttl_seconds(DEEPSEEK) is None
    assert cache_ttl_seconds("") is None
    assert explicit_cache_route(DEEPSEEK) is False


def _step(
    index: int,
    *,
    system: str,
    tools: str,
    prefix: str,
    cached: int | None,
    provider: str = "R",
) -> StepRecord:
    return StepRecord(
        index=index,
        prompt_tokens=1000,
        completion_tokens=10,
        model=ANTHROPIC,
        cached_tokens=cached,
        provider=provider,
        system_fingerprint=system,
        tools_fingerprint=tools,
        prefix_fingerprint=prefix,
    )


def _log(*steps: StepRecord) -> StepLog:
    log = StepLog()
    for step in steps:
        log.add(step)
    return log


def test_facts_compare_the_first_step_of_this_turn_with_the_first_step_of_the_previous_turn() -> None:
    previous_log = _log(_step(0, system="s1", tools="t1", prefix="p1", cached=900))
    previous = turn_record(ended_at=1000.0, model=ANTHROPIC, steplog=previous_log)
    now_log = _log(_step(0, system="s2", tools="t1", prefix="p2", cached=0))

    facts, gap = facts_for_turn(
        previous=previous,
        model=ANTHROPIC,
        steplog=now_log,
        now=1400.0,
        ttl_seconds=cache_ttl_seconds(ANTHROPIC),
        explicit_cache=explicit_cache_route(ANTHROPIC),
    )
    assert gap == 400.0
    assert facts.first_turn is False
    assert facts.system_changed is True
    assert facts.prefix_changed is True
    assert facts.tools_changed is False
    assert facts.model_changed is False
    # 400 s is beyond the 300 s lifetime, so the expiry is named before the changed prefix.
    assert classify_cache_miss(facts) == "ttl_expired"


def test_a_first_turn_has_no_gap_and_no_comparison() -> None:
    log = _log(_step(0, system="s", tools="t", prefix="p", cached=0))
    facts, gap = facts_for_turn(
        previous=None,
        model=ANTHROPIC,
        steplog=log,
        now=5.0,
        ttl_seconds=300.0,
        explicit_cache=True,
    )
    assert gap is None
    assert facts.first_turn is True
    assert classify_cache_miss(facts) == "first_turn"


def test_an_unrecorded_fingerprint_is_no_comparison_not_a_change() -> None:
    previous = {
        "ended_at": 0.0,
        "model": ANTHROPIC,
        "provider": "",
        "system_fingerprint": "",
        "tools_fingerprint": "",
        "prefix_fingerprint": "",
    }
    log = _log(_step(0, system="s", tools="t", prefix="p", cached=0, provider=""))
    facts, _gap = facts_for_turn(
        previous=previous,
        model=ANTHROPIC,
        steplog=log,
        now=10.0,
        ttl_seconds=300.0,
        explicit_cache=True,
    )
    assert facts.system_changed is None
    assert facts.prefix_changed is None
    assert facts.route_changed is None

def _real_step(index: int, system_prompt: str, tool_schema: list[dict[str, object]]) -> StepRecord:
    """A step stamped by the agent's own fingerprint functions, not by hand-typed strings."""
    from chimera.core.agent import _fingerprint_messages, _fingerprint_prefix, _fingerprint_value

    messages = [{"role": "system", "content": system_prompt}, {"role": "user", "content": "x"}]
    return _step(
        index,
        system=_fingerprint_messages(messages, "system"),
        tools=_fingerprint_value(tool_schema),
        prefix=_fingerprint_prefix(messages, tool_schema),
        cached=0,
    )


_READ = {"type": "function", "function": {"name": "read_file", "parameters": {"type": "object"}}}
_EDIT = {"type": "function", "function": {"name": "edit_file", "parameters": {"type": "object"}}}


def _cause(previous: StepLog, now: StepLog) -> str:
    record = turn_record(ended_at=0.0, model=ANTHROPIC, steplog=previous)
    facts, _ = facts_for_turn(
        previous=record, model=ANTHROPIC, steplog=now, now=10.0, ttl_seconds=300.0, explicit_cache=True
    )
    return classify_cache_miss(facts)


def test_a_change_of_only_the_tools_is_tools_changed_with_the_agents_own_fingerprints() -> None:
    before = _log(_real_step(0, "same system", [_READ]))
    after = _log(_real_step(0, "same system", [_READ, _EDIT]))
    assert _cause(before, after) == "tools_changed"


def test_a_change_of_only_the_system_message_is_prefix_changed() -> None:
    before = _log(_real_step(0, "system one", [_READ]))
    after = _log(_real_step(0, "system two", [_READ]))
    assert _cause(before, after) == "prefix_changed"


def test_an_unchanged_prompt_is_not_blamed_on_a_changed_one() -> None:
    before = _log(_real_step(0, "same", [_READ]))
    after = _log(_real_step(0, "same", [_READ]))
    assert _cause(before, after) == "unknown"


def test_a_narrower_tool_list_on_a_later_step_is_not_a_change_between_turns() -> None:
    # The tool router narrows the list on a later step of a turn; the turn's first step is what the
    # next turn is compared with, so the narrowing cannot read as the tools having changed.
    before = _log(_real_step(0, "same", [_READ, _EDIT]), _real_step(1, "same", [_READ]))
    after = _log(_real_step(0, "same", [_READ, _EDIT]), _real_step(1, "same", [_EDIT]))
    assert _cause(before, after) == "unknown"


def test_a_conversation_older_than_the_record_is_not_called_a_first_turn() -> None:
    log = _log(_step(0, system="s", tools="t", prefix="p", cached=0))
    facts, gap = facts_for_turn(
        previous=None,
        model=ANTHROPIC,
        steplog=log,
        now=5.0,
        ttl_seconds=300.0,
        explicit_cache=True,
        had_history=True,
    )
    assert gap is None
    assert facts.first_turn is False
    assert classify_cache_miss(facts) == "unknown"


def test_an_external_turn_leaves_a_pause_to_measure_and_nothing_to_compare() -> None:
    record = external_turn_record(ended_at=100.0, model="external:codex")
    log = _log(_step(0, system="s", tools="t", prefix="p", cached=0))
    facts, gap = facts_for_turn(
        previous=record, model=ANTHROPIC, steplog=log, now=700.0, ttl_seconds=300.0, explicit_cache=True
    )
    assert gap == 600.0
    assert facts.system_changed is None
    assert facts.tools_changed is None
    assert facts.model_changed is True
