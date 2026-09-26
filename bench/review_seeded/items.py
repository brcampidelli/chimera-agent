"""The 30 items: real small diffs from this repository's history, 20 with one seeded defect each.

Frozen in PREREGISTRATION.md before any model call.

**Selection.** Candidates are the commits in the last 1,500 that touch ``chimera/*.py`` with 6 to 60
changed lines in at most 2 Python files under ``chimera/`` and at most 4 files overall (86 commits,
listed most recent first). A candidate is excluded when its ``chimera/`` change is data or prose
only: catalogue price rows (729f3937, 2061f39b, 9fd2fbb6, b084133f, bb77d26e, fa80befa), a string
literal and comments (b0e2f43b), or a commit typed docs/test/bench/refactor. The first 30 eligible,
in list order, are the items. Only the ``chimera/*.py`` part of each commit is reviewed.

**Assignment.** ``random.Random(20260925).sample(ids, 10)`` picks the clean ten; the other twenty
get one defect each, written by hand into a line the commit itself added, so the defect is
introduced by the change under review. Each defect changes behaviour and is visible from the diff
(a contradiction with the code's own comment or contract counts as visible).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Seed:
    path: str
    old: str
    new: str
    defect: str
    category: str


@dataclass(frozen=True)
class Item:
    commit: str
    seed: Seed | None = None

    @property
    def kind(self) -> str:
        return "seeded" if self.seed else "clean"


IDS = (
    "b0f5c7ac", "08f94a6b", "8844b221", "0739496c", "8f529bf0", "397cb65e", "4f9ec26d", "b5f7e949",
    "4371af4a", "a0d564b0", "924aaa76", "876d7805", "d8662bd6", "d36ce2f3", "2b4354d0", "b7fd88c8",
    "d6e88890", "12116dda", "cf0ff439", "2157b03c", "86ee1267", "69718424", "ef8e0153", "110d9dbe",
    "e224de74", "e99e0cdc", "8ac471ce", "5b3ae2bb", "5b65b44b", "44d580a9",
)
ASSIGNMENT_SEED = 20260925
CLEAN = (
    "0739496c", "8f529bf0", "b5f7e949", "924aaa76", "d36ce2f3",
    "d6e88890", "cf0ff439", "2157b03c", "69718424", "e224de74",
)

SEEDS: dict[str, Seed] = {
    "b0f5c7ac": Seed(
        "chimera/fusion/verifier_select.py",
        "        return sum(values) / len(values) if values else None",
        "        return sum(values) / len(self.scorers) if values else None",
        "averages over every scorer, so an abstention counts as a zero again", "wrong divisor"),
    "08f94a6b": Seed(
        "chimera/orchestration/hierarchy.py",
        '        return f"{system}\\n\\n{self.identity}" if self.identity else system',
        '        return f"{system}\\n\\n{self.identity}" if self.identity else self.identity',
        "with no owner identity the stage prompt is replaced by an empty string", "wrong return"),
    "8844b221": Seed(
        "chimera/tui/app.py",
        '        if self.fuse and not self._said_fusion_skipped and report.model != "fusion":',
        '        if self.fuse and not self._said_fusion_skipped and report.model == "fusion":',
        "says fusion was skipped exactly when fusion answered", "inverted condition"),
    "397cb65e": Seed(
        "chimera/tools/browser.py",
        '                raise RuntimeError(f"{exc}; {install_exc}. {_CHROMIUM_HINT}") from exc',
        '                raise RuntimeError(f"{install_exc}. {_CHROMIUM_HINT}") from exc',
        "drops the launch error, the one message naming the cause", "information dropped"),
    "4f9ec26d": Seed(
        "chimera/integrations/mcp_client.py",
        "    return _TOOL_NAME.fullmatch(name) is not None",
        "    return _TOOL_NAME.match(name) is not None",
        "match anchors only the start, so a name with a sentence after a valid prefix passes",
        "anchoring"),
    "4371af4a": Seed(
        "chimera/tools/browser.py",
        '    return [str(driver_executable), str(driver_cli), "install", browser]',
        '    return [str(driver_cli), str(driver_executable), "install", browser]',
        "driver and cli swapped, so the install runs the script as the executable",
        "swapped arguments"),
    "a0d564b0": Seed(
        "chimera/core/spec_test.py",
        "    return rest[: closed.start()].strip()",
        "    return rest[: closed.end()].strip()",
        "the closing fence is kept in the extracted module, which then fails to compile",
        "boundary"),
    "876d7805": Seed(
        "chimera/providers/catalog.py",
        "    known = [entry.input_per_m, *(seen[0] for seen in entry.also_seen)]",
        "    known = [entry.input_per_m, *(seen[1] for seen in entry.also_seen)]",
        "compares an input price against the output prices seen", "wrong index"),
    "d8662bd6": Seed(
        "chimera/eval/anytime.py",
        "    return NormalDist().inv_cdf(1.0 - alpha / decisions)",
        "    return NormalDist().inv_cdf(1.0 - alpha * decisions)",
        "multiplies alpha, loosening each round instead of tightening it", "wrong operator"),
    "2b4354d0": Seed(
        "chimera/governance/profile.py",
        '            no_approver = "unattended"',
        '            no_approver = "owner_denies"',
        "an unattended API run is told the deployment denies by configuration", "wrong constant"),
    "b7fd88c8": Seed(
        "chimera/api/code_api.py",
        '    if (settings.approval_mode or "").strip().lower() != "allow":',
        '    if (settings.approval_mode or "").strip().lower() not in ("allow", "ask"):',
        "a server set to ask gets an approver that says yes to everything", "widened condition"),
    "12116dda": Seed(
        "chimera/core/autonomous.py",
        "                self.run_log.parent / \"discarded\" / f\"{attempt.run_id or 'run'}-{index}.diff\"",
        "                self.run_log.parent / \"discarded\" / f\"{attempt.run_id or 'run'}.diff\"",
        "every reverted attempt of a run overwrites the same file", "collision"),
    "86ee1267": Seed(
        "chimera/cli/main.py",
        "        loaded = len(mcp_connectors.names()) if mcp_connectors is not None else 0",
        "        loaded = len(mcp_connectors.names()) if mcp_connectors is None else 0",
        "calls .names() on None, and reports 0 when servers did connect", "inverted None check"),
    "ef8e0153": Seed(
        "chimera/integrations/mcp_client.py",
        "        return fence(sanitize_untrusted(result)) if result.strip() else result",
        "        return fence(result) if result.strip() else result",
        "remote output is fenced without defanging chat-template tokens", "missing call"),
    "110d9dbe": Seed(
        "chimera/kanban/lanes.py",
        "    return f\"[governance refused {len(denied)} call(s): {', '.join(names)}]\"",
        "    return f\"[governance refused {len(names)} call(s): {', '.join(names)}]\"",
        "counts distinct tools as refused calls", "wrong variable"),
    "e99e0cdc": Seed(
        "chimera/cli/main.py",
        "            context_budget=context_budget,\n            insist_on_action=True,",
        "            context_budget=None,\n            insist_on_action=True,",
        "solve accepts --context-budget and drops it", "value dropped"),
    "8ac471ce": Seed(
        "chimera/core/repomap.py",
        '    ".claude",\n}',
        '    ".claude/worktrees",\n}',
        "the ignore set holds names, so a path never matches and worktrees are still walked",
        "wrong constant"),
    "5b3ae2bb": Seed(
        "chimera/tools/files.py",
        "        except ValueError as exc:  # e.g. source with NUL bytes\n            return str(exc)",
        "        except ValueError as exc:  # e.g. source with NUL bytes\n            return None",
        "content with NUL bytes is accepted as a valid full file", "error swallowed"),
    "5b65b44b": Seed(
        "chimera/governance/policy.py",
        '            Decision.REVIEW,\n            "sending local file contents to a remote host",',
        '            Decision.ALLOW,\n            "sending local file contents to a remote host",',
        "the upload-egress rule allows what it exists to review", "wrong constant"),
    "44d580a9": Seed(
        "chimera/core/events.py",
        '    return f"{text[:head]}\\n… (+{len(text) - limit} chars)\\n{text[-tail:]}"',
        '    return f"{text[:head]}\\n… (+{len(text) - limit} chars)\\n{text[tail:]}"',
        "keeps everything after index tail instead of the last tail characters", "slice sign"),
}

ITEMS: tuple[Item, ...] = tuple(Item(c, SEEDS.get(c)) for c in IDS)
assert len(ITEMS) == 30 and {i.commit for i in ITEMS if i.seed is None} == set(CLEAN)
assert len(SEEDS) == 20
