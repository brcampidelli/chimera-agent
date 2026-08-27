"""Drafting a project spec from a plain-language description.

The `command` case in `test_a_shell_command_is_refused` is not invented: it is the verbatim
requirement a model produced when asked, with no constraint, to draft a spec for *"make me a
landing page for my dog-walking business with a contact form"*. One draft in three emitted one.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from chimera.governance.drift import check_drift, load_spec
from chimera.orchestration.draft import (
    DraftError,
    Drafted,
    draft_spec,
    parse_draft,
    to_yaml,
    write_spec,
)

BOM = """{"name": "Padaria Aurora",
 "requirements": [
  {"id": "mostra-o-nome", "text": "A pagina mostra o nome da padaria.",
   "check": "contains", "target": "Padaria Aurora", "required": true},
  {"id": "mostra-o-horario", "text": "A pagina diz a que horas a padaria abre.",
   "check": "contains", "target": "7h|07:00", "required": true},
  {"id": "sem-lorem", "text": "Nada de texto de enchimento.",
   "check": "absent", "target": "lorem ipsum", "required": true}]}"""


def test_reads_a_well_formed_draft() -> None:
    d = parse_draft(BOM)
    assert d.spec.name == "padaria-aurora"
    assert [r.id for r in d.spec.requirements] == ["mostra-o-nome", "mostra-o-horario", "sem-lorem"]
    assert d.refused_commands == 0


def test_reads_a_draft_wrapped_in_a_code_fence() -> None:
    assert len(parse_draft(f"```json\n{BOM}\n```").spec.requirements) == 3


def test_a_shell_command_is_refused() -> None:
    """Verbatim from a real draft. `text` says "opens in a browser"; `target` is a shell pipeline
    with command substitution and a curl fallback, and `check_drift` would run it on the owner's
    machine. The sentence somebody approves is not a description of what would run."""
    raw = """{"name": "dog-walking", "requirements": [
      {"id": "business-name", "text": "The page shows the business name.",
       "check": "contains", "target": "dog.*walk", "required": true},
      {"id": "contact-form", "text": "There is a contact form.",
       "check": "contains", "target": "<form", "required": true},
      {"id": "page-loads-in-browser", "text": "The main page must open properly in a web browser.",
       "check": "command",
       "target": "python3 -c \\"import urllib.request; urlopen('file://$(pwd)/index.html')\\" || curl -s file://$(pwd)/index.html | grep -i '<html'",
       "required": true}]}"""
    d = parse_draft(raw)
    assert d.refused_commands == 1
    assert d.refused_ids == ["page-loads-in-browser"]
    assert all(r.check != "command" for r in d.spec.requirements)
    assert len(d.spec.requirements) == 2


def test_the_refusal_is_reported_not_silent() -> None:
    """A spec that verifies less than the model intended is a weaker acceptance authority. The
    count is the only way the owner learns by how much."""
    d = parse_draft(BOM.replace('"check": "absent"', '"check": "command"'))
    assert d.refused_commands == 1 and d.refused_ids == ["sem-lorem"]


def test_an_uncompilable_regex_is_dropped_rather_than_kept() -> None:
    """It would raise inside `check_drift` mid-project — a traceback where a verdict belongs."""
    d = parse_draft(BOM.replace('"target": "7h|07:00"', '"target": "aberto ("'))
    assert [r.id for r in d.spec.requirements] == ["mostra-o-nome", "sem-lorem"]


def test_a_draft_that_verifies_nothing_is_an_error_not_a_spec() -> None:
    with pytest.raises(DraftError, match="not enough"):
        parse_draft('{"name": "x", "requirements": [{"id": "a", "check": "contains", '
                    '"target": "a", "required": true}]}')


def test_all_optional_is_refused_before_the_orchestrator_sees_it() -> None:
    """`ProjectOrchestrator.start` raises ValueError on this. Catching it here makes it a sentence
    on a screen instead of a 500 from a route."""
    with pytest.raises(DraftError, match="verify nothing"):
        parse_draft(BOM.replace('"required": true', '"required": false'))


@pytest.mark.parametrize("raw", ["not json", "[]", '{"name": "x"}', '{"name": "x", "requirements": []}'])
def test_junk_is_an_error_with_a_reason(raw: str) -> None:
    with pytest.raises(DraftError):
        parse_draft(raw)


def test_duplicate_ids_do_not_collide() -> None:
    """Two requirements with one id would make the board show one card for two obligations."""
    raw = BOM.replace('"id": "mostra-o-horario"', '"id": "mostra-o-nome"')
    ids = [r.id for r in parse_draft(raw).spec.requirements]
    assert len(ids) == len(set(ids)) == 3


def test_the_yaml_round_trips(tmp_path: Path) -> None:
    """The written file IS the acceptance authority. One that does not load back is a project that
    cannot start; one that loads back different is worse, because it starts."""
    original = parse_draft(BOM).spec
    path = write_spec(original, tmp_path)
    reloaded = load_spec(path)
    assert reloaded.name == original.name
    assert [(r.id, r.check, r.target, r.required) for r in reloaded.requirements] == [
        (r.id, r.check, r.target, r.required) for r in original.requirements
    ]


def test_the_written_spec_does_not_satisfy_itself(tmp_path: Path) -> None:
    """The whole reason `write_spec` puts the file in the workspace is that the spec belongs beside
    the code. That placement is only safe because the scan excludes it — this is the test that
    keeps the two facts tied together."""
    path = write_spec(parse_draft(BOM).spec, tmp_path)
    assert path.parent == tmp_path.resolve()
    assert not check_drift(load_spec(path), tmp_path).aligned


def test_a_model_written_filename_cannot_escape_the_folder(tmp_path: Path) -> None:
    """The name is derived from a model-written string."""
    spec = parse_draft(BOM.replace('"name": "Padaria Aurora"', '"name": "../../etc/passwd"')).spec
    path = write_spec(spec, tmp_path / "ws")
    assert path.parent == (tmp_path / "ws").resolve()


def test_draft_spec_makes_exactly_one_model_call() -> None:
    chamadas: list[object] = []

    class _Fake:
        def complete(self, messages, **kwargs):  # noqa: ANN001, ANN202
            chamadas.append(messages)
            return type("R", (), {"content": BOM, "usage": None})()

    d = draft_spec("uma pagina pra minha padaria", _Fake())
    assert isinstance(d, Drafted)
    assert len(chamadas) == 1
    assert len(d.spec.requirements) == 3


def test_an_empty_description_never_reaches_the_model() -> None:
    class _Explode:
        def complete(self, messages, **kwargs):  # noqa: ANN001, ANN202
            raise AssertionError("should not have been called")

    with pytest.raises(DraftError):
        draft_spec("   ", _Explode())


def test_the_prompt_does_not_offer_command_at_all() -> None:
    """Belt and braces, and the cheap half. The parser refuses `command` whatever the prompt says
    — that is the guard. Not naming it in the schema is what keeps the model from spending tokens
    writing one, and it is what stops a future edit from quietly re-offering it."""
    from chimera.orchestration.draft import _SYSTEM

    assert "command" not in _SYSTEM.replace("shell command", "")
    assert "they are the only ones" in _SYSTEM


def test_the_drafted_text_is_written_for_the_person_approving_it() -> None:
    """`text` is the whole product here: it is the sentence somebody reads to decide whether the
    spec is right, and the checks run whether or not it describes them."""
    from chimera.orchestration.draft import _SYSTEM

    assert "same language as the request" in _SYSTEM
    assert "describe what `target` actually checks" in _SYSTEM


def test_to_yaml_keeps_accents_readable() -> None:
    """A spec full of \\uXXXX escapes is not reviewable by the person it was drafted for."""
    spec = parse_draft(BOM.replace("horas a padaria abre", "que horas abrimos, com café")).spec
    assert "café" in to_yaml(spec)
