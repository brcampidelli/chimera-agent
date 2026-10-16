"""Owner deployment policy is ordered, validated as a whole, and never a grant source.

A later allow may undo an earlier deny from the same owner-authored file, but it only
changes that policy's decision. Callers continue to enforce their independent controls.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from chimera.deployment_policy import PolicyError, load_policy


def test_policy_last_matching_rule_wins_for_its_action_and_resource(tmp_path: Path) -> None:
    policy_file = tmp_path / "owner-policy.json"
    policy_file.write_text(
        '{"rules": ['
        '{"action": "provider.use", "resource": "*", "effect": "deny"},'
        '{"action": "provider.use", "resource": "openrouter", "effect": "allow"},'
        '{"action": "tool.use", "resource": "openrouter", "effect": "allow"}'
        ']}',
        encoding="utf-8",
    )
    policy = load_policy(SimpleNamespace(home=tmp_path, policy_file=policy_file))

    assert not policy.denied("provider.use", "openrouter")
    assert policy.denied("provider.use", "anthropic")
    # Rules for another action cannot turn the provider decision into an authorization grant.
    assert policy.denied("tool.use", "openrouter") is False
    assert len(policy.effective()) == 3


def test_policy_rejects_the_entire_file_when_a_rule_is_invalid(tmp_path: Path) -> None:
    policy_file = tmp_path / "owner-policy.json"
    policy_file.write_text(
        '{"rules": ['
        '{"action": "provider.use", "resource": "*", "effect": "deny"},'
        '{"action": "network.open", "resource": "*", "effect": "allow"}'
        ']}',
        encoding="utf-8",
    )
    with pytest.raises(PolicyError, match="Invalid deployment policy"):
        load_policy(SimpleNamespace(home=tmp_path, policy_file=policy_file))
