"""Two writers, one audit file. The chain has to survive that, and it did not.

:meth:`AuditLog.__init__` read the head digest and the entry count once and advanced them in memory
only, so two instances over one path each believed they were the only writer: both claimed the same
``seq``, both chained onto the same ``prev``. Measured on the code before this file existed, four
alternating appends::

    entries=4 seqs=[0, 0, 1, 1]
    verify -> ok=False broken_at=1 reason='broken link to previous entry'

That direction of failure is the dangerous one. ``GET /api/governance/audit`` feeds the Security
screen, so a log nobody had touched showed itself as tampered — which destroys confidence in the
file exactly as thoroughly as a tamper nobody detects, and is far easier to trigger.

Two writers is the ordinary configuration here, not a corner. ``assemble_registry`` builds a fresh
``AuditLog`` per request and the API serves each request on its own thread; ``chimera serve`` runs
the cron daemon and the HTTP gateway as separate processes over one home, all day. Both cases are
covered below.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

import chimera.governance.audit as audit_mod
from chimera.governance.audit import AuditLog

LIMITE_SEGUNDOS = 90.0
"""Ceiling for anything here that waits, so a lost writer fails the run instead of hanging it."""


def _chain_is_sound(path: Path, esperado: int) -> None:
    """Every claim this file makes about a finished log, in one place.

    ``list(range(n))`` is the strong assertion: it says the sequence numbers are unique, ascending,
    and gapless in one line. The uniqueness check above it is redundant on purpose — it is the
    property being defended, and a failure should name it rather than make the reader derive it.
    """
    log = AuditLog(path)
    entradas = log.entries()
    seqs = [entrada["seq"] for entrada in entradas]

    assert len(entradas) == esperado, f"esperava {esperado} entradas, achei {len(entradas)}"
    assert len(set(seqs)) == len(seqs), f"seq duplicado: {seqs}"
    assert seqs == list(range(esperado)), f"seq nao crescente/contiguo: {seqs}"

    check = log.verify()
    assert check.ok, f"cadeia reprovada em {check.broken_at}: {check.reason}"
    assert check.checked == esperado
    assert check.unchained == 0


def test_two_logs_on_one_path_write_one_unbroken_chain(tmp_path: Path) -> None:
    """The reported failure, reduced: two instances, alternating appends, no threads involved."""
    path = tmp_path / "audit.jsonl"
    primeiro, segundo = AuditLog(path), AuditLog(path)

    for indice in range(6):
        escritor = primeiro if indice % 2 == 0 else segundo
        escritor.record("governance", {"decision": "review", "n": indice})

    _chain_is_sound(path, 6)


def test_threads_each_building_their_own_log_do_not_collide(tmp_path: Path) -> None:
    """The API's actual shape: one ``AuditLog`` per request, one thread per request, one file.

    Each instance is built *before* the barrier releases, so every one of them snapshots the same
    empty file. That is the stale-head condition the fix has to survive, made deterministic rather
    than waited for.
    """
    path = tmp_path / "audit.jsonl"
    fios, por_fio = 6, 8
    largada = threading.Barrier(fios, timeout=LIMITE_SEGUNDOS)
    falhas: list[BaseException] = []

    def trabalhador() -> None:
        log = AuditLog(path)  # what `assemble_registry` does, once per request
        try:
            largada.wait()
            for indice in range(por_fio):
                log.record("governance", {"decision": "review", "n": indice})
        except BaseException as exc:  # noqa: BLE001 - re-raised on the main thread below
            falhas.append(exc)

    equipe = [threading.Thread(target=trabalhador) for _ in range(fios)]
    for fio in equipe:
        fio.start()
    for fio in equipe:
        fio.join(timeout=LIMITE_SEGUNDOS)
        assert not fio.is_alive(), "um escritor nao terminou dentro do limite"

    assert not falhas, f"escritor levantou: {falhas[0]!r}"
    _chain_is_sound(path, fios * por_fio)


def test_the_file_lock_never_arbitrates_between_two_threads_of_one_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two threads of one process must be separated *before* they reach the OS file lock.

    This is the one property the test above cannot show, because on Linux ``flock`` happens to
    serialise two descriptors of the same process correctly, so the process lock looks redundant
    there. It is not redundant on Windows, where it was measured: a second handle on the same lock
    file from the SAME process blocks for 9.1s and then raises ``[Errno 36] Resource deadlock
    avoided``, at which point ``locked()`` takes its documented degraded path and writes *unlocked*.
    On that platform the file lock would be arbitrating a fight it cannot win, in the case that
    dominates this file.

    So the claim under test is platform-independent and checkable here: no two threads are ever
    inside ``locked()`` at the same time. The sleep widens the window enough for an overlap to be
    observed rather than raced past.
    """
    path = tmp_path / "audit.jsonl"
    real = audit_mod.locked
    contagem = threading.Lock()
    dentro = pico = 0

    @contextmanager
    def espiao(alvo: Path) -> Iterator[bool]:
        nonlocal dentro, pico
        with contagem:
            dentro += 1
            pico = max(pico, dentro)
        try:
            with real(alvo) as tomado:
                time.sleep(0.002)
                yield tomado
        finally:
            with contagem:
                dentro -= 1

    monkeypatch.setattr(audit_mod, "locked", espiao)

    fios = 5
    largada = threading.Barrier(fios, timeout=LIMITE_SEGUNDOS)

    def trabalhador() -> None:
        log = AuditLog(path)
        largada.wait()
        log.record("governance", {"decision": "review"})

    equipe = [threading.Thread(target=trabalhador) for _ in range(fios)]
    for fio in equipe:
        fio.start()
    for fio in equipe:
        fio.join(timeout=LIMITE_SEGUNDOS)
        assert not fio.is_alive()

    assert pico == 1, f"{pico} threads entraram no lock de arquivo ao mesmo tempo"
    _chain_is_sound(path, fios)


# The child of the cross-process test. Held as source rather than as a function because the two
# portable ways to get a real second process both fail here: `fork` is POSIX-only, and `spawn`
# re-imports the worker's module, which pytest loaded from a directory the fresh child has no reason
# to have on its path. A subprocess sidesteps both, and is the shape production actually has — the
# cron daemon and the gateway are separate processes, not forks of one.
_FILHO = """
import sys, time
from pathlib import Path
from chimera.governance.audit import AuditLog

destino, largada, pronto, quantos = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3]), int(sys.argv[4])
log = AuditLog(destino)          # snapshot taken before any sibling writes: the stale-head condition
pronto.write_text("1", encoding="utf-8")
limite = time.monotonic() + 60
while not largada.exists():      # every child appends from the same instant
    if time.monotonic() > limite:
        raise SystemExit("largada nunca veio")
    time.sleep(0.005)
for indice in range(quantos):
    log.record("governance", {"decision": "review", "n": indice})
"""


def test_separate_processes_append_to_one_chain(tmp_path: Path) -> None:
    """``chimera serve``: the cron daemon and the HTTP gateway write this file from two processes.

    The children inherit this interpreter's ``chimera`` explicitly rather than whatever the venv
    happens to have installed. Without that, an editable install pointing somewhere else would let
    the child exercise a different copy of the code and the test would pass while proving nothing —
    which is not hypothetical: the venv used to develop this fix points at a different worktree.
    """
    path = tmp_path / "audit.jsonl"
    largada = tmp_path / "largada"
    prontos = tmp_path / "prontos"
    prontos.mkdir()
    processos_n, por_processo = 3, 6

    raiz = Path(audit_mod.__file__).resolve().parents[2]
    ambiente: dict[str, str] = {**os.environ, "PYTHONPATH": str(raiz)}

    filhos = [
        subprocess.Popen(
            [
                sys.executable, "-c", _FILHO,
                str(path), str(largada), str(prontos / str(indice)), str(por_processo),
            ],
            env=ambiente,
            stderr=subprocess.PIPE,
            text=True,
        )
        for indice in range(processos_n)
    ]

    try:
        limite = time.monotonic() + LIMITE_SEGUNDOS
        while len(list(prontos.iterdir())) < processos_n:
            assert time.monotonic() < limite, "os filhos nunca ficaram prontos"
            time.sleep(0.02)
        largada.write_text("go", encoding="utf-8")

        for filho in filhos:
            _, erro = filho.communicate(timeout=LIMITE_SEGUNDOS)
            assert filho.returncode == 0, f"filho falhou: {erro}"
    finally:
        for filho in filhos:
            if filho.poll() is None:  # pragma: no cover - only on a timeout above
                filho.kill()

    _chain_is_sound(path, processos_n * por_processo)


def test_a_torn_final_line_does_not_block_an_open_log_from_appending(tmp_path: Path) -> None:
    """Re-reading the head opened a way to brick the log, and it must stay shut.

    Before this fix ``record()`` read nothing, so an open ``AuditLog`` kept appending no matter what
    the file had become — measured against the base commit, a final line torn by a crash left
    ``record()`` on an already-open instance working. The head now comes from disk, so that same
    torn line is input to every later append; taken through :meth:`AuditLog.entries` it would raise,
    and one interrupted write would stop every write after it. Trading a broken chain for a log that
    refuses to record is not a fix.

    Scope, stated rather than implied: this covers the *open* instance, which is the writer a
    neighbour's crash surprises. Constructing a **new** ``AuditLog`` over a torn file still raises,
    as it did before this change — the constructor parses the whole file and always has. That is
    pre-existing and deliberately left alone here.
    """
    path = tmp_path / "audit.jsonl"
    log = AuditLog(path)
    log.record("governance", {"decision": "review"})
    with path.open("a", encoding="utf-8") as handle:
        handle.write('{"seq": 1, "type": "governance", "deci')  # killed mid-write, no newline

    entrada: dict[str, Any] = log.record("governance", {"decision": "block"})

    assert entrada["seq"] == 2  # both lines counted, including the one it could not parse
    assert path.read_text(encoding="utf-8").splitlines()[-1].endswith("}")
