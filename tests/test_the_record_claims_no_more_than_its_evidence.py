"""Sentences that promised more than the code or the evidence, pinned so they do not come back.

Study 30 corrected a set of docstrings and docs (S30-15 to S30-21). A review of that pass found the
corrections themselves overreaching in places: a rule moved onto a bench that disclaims testing it,
a "null" scoped to one model when the bench measured two, a skill item that kept the very sentence it
was amending, a stale "production bot" claim left in a second file, and a docs bullet that read as a
boundary the code does not draw. A prose claim has no other test, so each one gets a check here:
the overreaching wording is absent, and the qualifier that replaced it is present.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def _flat(text: str) -> str:
    """Collapse line wrapping (and docstring indentation) so a sentence is one searchable string."""
    return re.sub(r"\s+", " ", text)


def test_the_family_rule_is_not_said_to_rest_on_a_bench_that_disclaims_testing_it() -> None:
    # bench/review_reviewer/PREREGISTRATION.md, "What this cannot show": no diff there was written by
    # a reviewer model, so it compares reviewers and cannot test the family rule.
    prereg = _flat(_read("bench/review_reviewer/PREREGISTRATION.md"))
    assert "No diff here was written by any reviewer model" in prereg  # the premise of this test
    for rel in ("chimera/review/family.py", "bench/PLAN-study25-system-prompts.md"):
        text = _flat(_read(rel))
        assert "which measured it" not in text, rel
        assert "40 seeded defects" not in text, rel
        assert "on what this repository measured" not in text, rel
        assert "20 diffs x 2" in text, rel
    assert "has not tested the rule" in _flat(_read("chimera/review/family.py"))
    assert "does not test the rule" in _flat(_read("bench/PLAN-study25-system-prompts.md"))


def test_the_catch_all_item_no_longer_sends_unsure_readings_to_the_catch_all() -> None:
    skill = _flat(_read("skills/system-one-design/SKILL.md"))
    assert "is where an unsure reading goes" not in skill
    assert "unreliable in both directions" in skill
    assert "`noul` per candidate when rejection matters" in skill


def test_no_settings_text_says_the_production_bot_runs_on_the_open_default() -> None:
    # True until 0.64.2 set an allowlist on the VPS; allowlist.py was corrected, config.py was not.
    for rel in (
        "chimera/config.py",
        "chimera/server/allowlist.py",
        "tests/test_a_bot_answers_only_the_people_its_owner_listed.py",
    ):
        text = _flat(_read(rel))
        assert "production bot runs on exactly that default" not in text, rel
        assert "silence the production bot" not in text, rel
        assert "until 0.64.2" in text, rel


def test_dropping_server_instructions_is_not_presented_as_a_boundary() -> None:
    # The same server's tool descriptions reach the model as written (mcp_client list_tools).
    client = _read("chimera/integrations/mcp_client.py")
    assert 'description=tool.description or ""' in client  # the premise: passed through as given
    assert "dropped on purpose" in client  # the decision is recorded where it is made
    doc = _flat(_read("docs/mcp.md"))
    assert "is **not** a boundary against server-written text" in doc
    assert "tool names and descriptions reach the model as the server wrote them" in doc


def test_the_local_decider_names_every_caller_that_sends_it_unsanitised_text() -> None:
    from chimera.decisions.local import LocalLogprobBackend

    doc = _flat(LocalLogprobBackend.body.__doc__ or "")
    assert "which is the REVIEW band" not in doc  # the old one-surface scope
    for caller in ("REVIEW band", "``decide`` tool", "grounded_state", "ON by default"):
        assert caller in doc, caller


def test_agents_md_does_not_say_the_gates_enforce_what_the_file_says() -> None:
    import chimera.core.agents_md as agents_md

    doc = _flat(agents_md.__doc__ or "")
    assert "the enforcement lives where it always did" not in doc
    assert "does not arm the taint ledger" in doc
    assert "enforced by nothing" in doc


def test_the_band_says_the_action_it_judges_is_written_by_the_agent() -> None:
    import chimera.governance.band as band

    doc = _flat(band.__doc__ or "")
    assert "the action is WRITTEN by the agent being judged" in doc
    assert "2609.19587" in doc


def test_bench_records_scope_their_closed_and_zero_percent_claims() -> None:
    injection = _flat(_read("bench/injection/RESULTS.md"))
    assert "Only the QUERY STRING is read, and only in a tainted run" in injection
    assert "2610.01768" in injection
    poison = _flat(_read("bench/memory_poison/RESULTS.md"))
    assert '"unmarked 0%" is a fact about the label' in poison
    assert "2610.00450" in poison


def test_the_audit_log_is_called_testimony_not_evidence() -> None:
    security = _flat(_read("SECURITY.md"))
    assert "**testimony, not evidence**" in security
    assert "2609.32495" in security
