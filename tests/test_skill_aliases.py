"""Speaking the catalogued skills' tool vocabulary — and the three ways that could go wrong.

A translation layer between an agent and other people's instructions has an obvious job and three
unobvious duties: it must not become a way around the allowlist, it must not drop the taint marker
on the way through, and it must not answer to a name it cannot actually serve. The tests are mostly
about those.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from chimera.skills.aliases import (
    MISSING,
    SkillView,
    adapt_registry,
    foreign_names,
    glossary,
    install_into,
    missing_names,
    translated_names,
)
from chimera.tools.base import Tool, is_untrusted_output
from chimera.tools.registry import ToolRegistry


class _Fake(Tool):
    def __init__(self, name: str, *, untrusted: bool = False) -> None:
        self.name = name
        self.description = f"the {name} tool"
        self.parameters = {"type": "object", "properties": {}}
        self.untrusted_output = untrusted
        self.calls: list[dict[str, Any]] = []

    def run(self, **kwargs: Any) -> str:
        self.calls.append(kwargs)
        return f"{self.name}:{sorted(kwargs.items())}"


def _registry(*names: str, untrusted: tuple[str, ...] = ()) -> ToolRegistry:
    reg = ToolRegistry()
    for name in names:
        reg.register(_Fake(name, untrusted=name in untrusted))
    return reg


# --- the property that matters most -------------------------------------------------------------


def test_an_alias_cannot_reach_a_tool_the_registry_does_not_have() -> None:
    # `scrape` is absent — removed by posture, an allowlist, or a missing credential. `web_extract`
    # must be absent too, because an alias that conjured the capability back would be a hole in
    # the gate that let the gate keep reporting itself closed.
    without = adapt_registry(_registry("grep", "read_file"))

    assert "web_extract" not in {t.name for t in without.tools()}
    assert "search_files" in {t.name for t in without.tools()}, "grep is here, so its alias is too"


def test_the_untrusted_marker_survives_the_wrapper() -> None:
    reg = _registry("scrape", "grep", untrusted=("scrape",))

    adapted = adapt_registry(reg)
    web_extract = next(t for t in adapted.tools() if t.name == "web_extract")

    # `is_untrusted_output` walks `.inner`, and its own docstring says a wrapper that forgets is
    # the difference between a defended run and an undefended one.
    assert is_untrusted_output(web_extract) is True
    assert web_extract.untrusted_output is True
    assert is_untrusted_output(next(t for t in adapted.tools() if t.name == "search_files")) is False


def test_a_real_tool_of_ours_is_never_shadowed() -> None:
    # If a name we already use ever collides with one of theirs, ours wins — an alias replacing a
    # real tool would change behaviour nobody asked to change.
    ours = _Fake("web_extract")
    reg = ToolRegistry()
    reg.register(ours)
    reg.register(_Fake("scrape"))

    adapted = adapt_registry(reg)

    assert next(t for t in adapted.tools() if t.name == "web_extract") is ours


# --- the translations ----------------------------------------------------------------------------


def test_arguments_are_translated_not_just_the_name() -> None:
    reg = _registry("grep")
    adapted = adapt_registry(reg)
    search = next(t for t in adapted.tools() if t.name == "search_files")

    search.run(pattern="def foo", path="src/", file_glob="*.py")

    # The whole reason this is not a table of name pairs: `search_files` says `file_glob` and
    # `grep` says `glob`, so a name-only alias produces a tool that exists and fails on first use.
    inner = next(t for t in reg.tools() if t.name == "grep")
    assert inner.calls == [{"pattern": "def foo", "path": "src/", "glob": "*.py"}]


def test_a_list_of_urls_becomes_one_fetch_each_labelled_by_url() -> None:
    reg = _registry("scrape")
    web_extract = next(t for t in adapt_registry(reg).tools() if t.name == "web_extract")

    out = web_extract.run(urls=["https://a.example", "https://b.example"])

    assert next(t for t in reg.tools() if t.name == "scrape").calls == [
        {"url": "https://a.example"},
        {"url": "https://b.example"},
    ]
    # Labelled, because a concatenation without the URLs is a wall of text nobody can attribute.
    assert "## https://a.example" in out and "## https://b.example" in out


def test_too_many_urls_is_refused_rather_than_silently_shortened() -> None:
    reg = _registry("scrape")
    web_extract = next(t for t in adapt_registry(reg).tools() if t.name == "web_extract")

    out = web_extract.run(urls=[f"https://{i}.example" for i in range(50)])

    # A quietly truncated list comes back reading like "those pages had nothing in them".
    assert "error" in out and "50" in out
    assert next(t for t in reg.tools() if t.name == "scrape").calls == []


def test_browser_navigate_is_reported_missing_rather_than_half_answered() -> None:
    reg = _registry("browser")

    adapted = adapt_registry(reg)

    # It was an adapter for one release of this file and then was not, which is the point worth
    # keeping: its target is itself an optional tool, and — measured — every skill that mentions
    # it is blocked by `browser_vision` too, so answering to the name bought a caller nothing and
    # cost a description for a tool that is not always there. Better absent and SAID.
    assert "browser_navigate" not in {t.name for t in adapted.tools()}
    assert "browser_navigate" in MISSING


# --- reading an installed skill's own files -------------------------------------------------------


def _bundle(root: Path, name: str = "demo") -> Path:
    skill = root / name
    (skill / "references").mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: demo\n---\nthe procedure", encoding="utf-8")
    (skill / "references" / "forms.md").write_text("the reference", encoding="utf-8")
    return skill


def test_skill_view_reads_a_bundle_that_read_file_cannot(tmp_path: Path) -> None:
    _bundle(tmp_path)
    view = SkillView(tmp_path)

    # This is why it exists rather than being an alias of `read_file`: that one is rooted in the
    # WORKSPACE and refuses anything outside it, and a bundle lives in the home directory. The
    # first version of the prompt line told the agent to open a path its own file tool would not
    # resolve, so progressive disclosure had never worked at all.
    assert "the procedure" in view.run(name="demo")
    assert "the reference" in view.run(name="demo", file_path="references/forms.md")


@pytest.mark.parametrize(
    "args",
    [
        {"name": "demo", "file_path": "../../../etc/passwd"},
        {"name": "../demo"},
        {"name": ""},
        {"name": "nope"},
    ],
)
def test_skill_view_stays_inside_the_skill(args: dict[str, str], tmp_path: Path) -> None:
    _bundle(tmp_path)
    outside = tmp_path.parent / "secret.txt"
    outside.write_text("not yours", encoding="utf-8")

    out = SkillView(tmp_path).run(**args)

    # The arguments come from a model reading a stranger's instructions.
    assert out.startswith("error:")
    assert "not yours" not in out


def test_what_a_skill_ships_is_treated_as_what_it_is(tmp_path: Path) -> None:
    _bundle(tmp_path)

    # Downloaded from a stranger's repository. A reference file is not the SKILL.md a person read
    # before switching the skill on, and relabelling where text came from is not a fix for noise.
    assert SkillView(tmp_path).untrusted_output is True


def test_a_long_reference_says_it_was_cut(tmp_path: Path) -> None:
    skill = _bundle(tmp_path)
    (skill / "big.md").write_text("x" * (SkillView.MAX_CHARS + 500), encoding="utf-8")

    out = SkillView(tmp_path).run(name="demo", file_path="big.md")

    # Half a procedure, silently, is a procedure that goes wrong at step nine with no sign of why.
    assert out.endswith("characters]") and "truncated" in out


# --- what we cannot translate ---------------------------------------------------------------------


def test_names_with_no_equivalent_are_reported_not_dropped() -> None:
    names = foreign_names("Use `delegate_task` and search_files(pattern) to do it.")

    assert translated_names(names) == ["search_files"]
    assert missing_names(names) == ["delegate_task"]
    lines = glossary(names)
    # An agent told "there is no delegate_task here" adapts or reports; one left to discover it
    # calls a tool that is not there and tries again.
    assert len(lines) == 1 and "delegate_task" in lines[0] and "not available" in lines[0]


def test_reading_a_body_ignores_the_code_samples_around_the_tool_names() -> None:
    body = """
    Call web_extract(urls=["x"]) and then json.loads(text), os.path.join(a, b),
    my_helper_function(), `some_other_thing`.
    """

    # A generous pattern over other people's markdown would otherwise collect every snake_case
    # function in every sample and report them as tools we lack.
    assert foreign_names(body) == ["web_extract"]


def test_every_missing_name_says_what_it_would_have_done() -> None:
    for name, what in MISSING.items():
        assert what and not what.endswith("."), name
    # A person reading "delegate_task: not available" learns nothing they could act on.
    assert "sub-agent" in MISSING["delegate_task"]


def test_install_into_reports_what_it_added() -> None:
    reg = _registry("scrape", "grep")

    added = install_into(reg, bundles_root=None)

    assert set(added) == {"web_extract", "search_files"}
    assert "skill_view" not in added, "no bundles root, no reading of bundles"
