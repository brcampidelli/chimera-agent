"""Reading back what the scheduled jobs answered.

Every dispatch has appended a line to `cron_results.jsonl` since the daemon existed, and the only
code that ever touched that file was the code that wrote it. A schedule could run every night for a
month, answer well every time, and its owner had no way to read one — while the screen that created
it promised to "save each result".

The reading is from the END of the file on purpose. It grows for the life of the install and is
never rotated: an hourly job writes 8,760 lines a year and an answer can be a page. Loading it whole
to show the last ten is a cost that arrives eighteen months later, on the machine of whoever left
the app running longest.
"""

from __future__ import annotations

import json
from pathlib import Path

from chimera.scheduler.results import TAIL_BYTES, load_results


def _escrever(path: Path, registros: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        for r in registros:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")


def _registro(**over) -> dict:
    base = {
        "at": 1_787_000_000.0,
        "id": "j1",
        "name": "resumo do site",
        "action": "liste os arquivos",
        "deliver_to": None,
        "answer": "três arquivos",
    }
    return {**base, **over}


def test_a_missing_file_is_no_results_rather_than_an_error(tmp_path: Path) -> None:
    """The state of every install until a schedule has fired once."""
    assert load_results(tmp_path / "nope.jsonl") == []


def test_the_newest_answer_comes_first(tmp_path: Path) -> None:
    """A screen shows the latest. Reversing at the caller means every caller reverses, and one of
    them forgets."""
    alvo = tmp_path / "cron_results.jsonl"
    _escrever(alvo, [_registro(at=1.0, answer="antigo"), _registro(at=2.0, answer="recente")])

    assert [r.answer for r in load_results(alvo)] == ["recente", "antigo"]


def test_it_narrows_to_one_schedule(tmp_path: Path) -> None:
    alvo = tmp_path / "cron_results.jsonl"
    _escrever(alvo, [_registro(id="j1", answer="a"), _registro(id="j2", answer="b")])

    assert [r.answer for r in load_results(alvo, job_id="j2")] == ["b"]


def test_delivery_absent_and_delivery_failed_are_different_answers(tmp_path: Path) -> None:
    """One means "nobody asked for delivery", the other means "we tried and could not". A screen
    that renders both as "not delivered" is telling somebody their webhook is broken when they
    never set one."""
    alvo = tmp_path / "cron_results.jsonl"
    _escrever(
        alvo,
        [
            _registro(at=1.0, answer="sem webhook"),
            _registro(at=2.0, answer="falhou", delivered=False, delivery_detail="HTTP 401"),
            _registro(at=3.0, answer="entregue", delivered=True, delivery_detail="HTTP 204"),
        ],
    )

    por_resposta = {r.answer: r for r in load_results(alvo)}
    assert por_resposta["sem webhook"].delivered is None
    assert por_resposta["falhou"].delivered is False
    assert por_resposta["falhou"].delivery_detail == "HTTP 401"
    assert por_resposta["entregue"].delivered is True


def test_a_torn_line_is_skipped_rather_than_fatal(tmp_path: Path) -> None:
    """This file is appended from a background thread, so a half-written final line is an ordinary
    event — not a reason for the screen to fail."""
    alvo = tmp_path / "cron_results.jsonl"
    _escrever(alvo, [_registro(answer="bom")])
    with alvo.open("a", encoding="utf-8") as fh:
        fh.write('{"at": 3.0, "answer": "cort')

    assert [r.answer for r in load_results(alvo)] == ["bom"]


def test_it_does_not_read_the_whole_file(tmp_path: Path) -> None:
    """The property the tail read exists for.

    A file well over the window still answers, and answers with the LATEST lines — which is what a
    naive `read_text().splitlines()[-n:]` also does, at the cost of loading every byte. The
    assertion that separates them is the one below: the first line written is not in the window,
    so a reader that loaded everything would have found it.
    """
    alvo = tmp_path / "cron_results.jsonl"
    _escrever(alvo, [_registro(at=1.0, answer="PRIMEIRO", action="x" * 2000)])
    _escrever(alvo, [_registro(at=float(i), action="y" * 2000) for i in range(2, 400)])
    _escrever(alvo, [_registro(at=9999.0, answer="ULTIMO")])

    assert alvo.stat().st_size > TAIL_BYTES, "the fixture is not big enough to test the window"

    resultados = load_results(alvo, limit=500)
    respostas = [r.answer for r in resultados]
    assert respostas[0] == "ULTIMO"
    assert "PRIMEIRO" not in respostas, "it read past the window"


def test_the_partial_first_line_of_the_window_is_dropped(tmp_path: Path) -> None:
    """A read that starts mid-file starts mid-line, and half a JSON object parses as nothing at
    best and as something wrong at worst."""
    alvo = tmp_path / "cron_results.jsonl"
    _escrever(alvo, [_registro(at=float(i), action="z" * 3000) for i in range(300)])

    # Every result that comes back is whole: the fields are the ones a record has.
    for r in load_results(alvo, limit=500):
        assert r.job_id == "j1"
        assert r.name == "resumo do site"


def test_the_limit_is_honoured(tmp_path: Path) -> None:
    alvo = tmp_path / "cron_results.jsonl"
    _escrever(alvo, [_registro(at=float(i)) for i in range(20)])

    assert len(load_results(alvo, limit=5)) == 5
