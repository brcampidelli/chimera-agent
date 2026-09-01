"""Nothing recorded when a fact was written, so nothing could say a citation had gone stale.

`value.rank` uses POSITION in the list as the recency proxy and says so in its own docstring — which
is the honest thing to write when the age is not available, and the age was not available: nothing
in `MemoryItem` carried it.

The cost is not the ranking. It is that a memory saying `agent.py:646 does X` reads exactly the same
on the day it is written and six months later, after the line has moved — and a `file:line` citation
makes a stale claim sound MORE authoritative, not less.

`None` is the migration, exactly as `project` is one field above: a fact written before this existed
has no age, and stamping it with the moment of the upgrade would make every old memory read as
written today, which is the opposite of true.
"""

from __future__ import annotations

import json
from pathlib import Path

from chimera.memory.manager import MemoryManager
from chimera.memory.models import MemoryItem
from chimera.memory.store import MemoryStore


def _manager(tmp_path: Path, *, agora: float = 1_000_000.0) -> MemoryManager:
    return MemoryManager(MemoryStore(tmp_path / "m.json"), clock=lambda: agora)


def test_a_new_fact_carries_the_moment_it_was_written(tmp_path: Path) -> None:
    item = _manager(tmp_path).add("o projeto usa Postgres")

    assert item.created_at == 1_000_000.0


def test_it_survives_the_round_trip(tmp_path: Path) -> None:
    """A timestamp held only in memory answers nothing — the question is about a fact on disk that
    has been there a while."""
    _manager(tmp_path).add("o projeto usa Postgres")

    voltou = MemoryStore(tmp_path / "m.json").all()[0]

    assert voltou.created_at == 1_000_000.0


def test_remember_stamps_it_too(tmp_path: Path) -> None:
    """`remember` is the path the chat and the app both use, and `add` is what it calls — pinned
    because a stamp only on the direct path would leave every user-written fact ageless."""
    _op, item = _manager(tmp_path).remember("lembre que o voo é dia 12")

    assert item.created_at == 1_000_000.0


def test_a_fact_written_before_the_field_existed_has_no_age(tmp_path: Path) -> None:
    """The migration. `None` means "we do not know", and that is the only true answer for a row
    written by a version that did not record it."""
    caminho = tmp_path / "m.json"
    caminho.write_text(
        json.dumps([{"id": "m1", "kind": "semantic", "content": "algo antigo"}]), encoding="utf-8"
    )

    assert MemoryStore(caminho).all()[0].created_at is None


def test_an_old_fact_is_not_stamped_with_today(tmp_path: Path) -> None:
    """The failure mode of a careless migration: filling the gap with `now` makes a six-month-old
    memory read as written this morning — worse than not knowing, because it is confidently wrong.
    """
    caminho = tmp_path / "m.json"
    caminho.write_text(
        json.dumps([{"id": "m1", "kind": "semantic", "content": "algo antigo"}]), encoding="utf-8"
    )
    loja = MemoryStore(caminho)

    loja.add(MemoryItem(id="m2", content="algo novo", created_at=2_000_000.0))

    idades = {item.id: item.created_at for item in loja.all()}
    assert idades == {"m1": None, "m2": 2_000_000.0}


def test_the_clock_is_injected(tmp_path: Path) -> None:
    """A timestamp nothing can control is a timestamp nothing can assert. `failover.py` takes its
    clock the same way, for the same reason."""
    primeiro = _manager(tmp_path, agora=10.0).add("a")
    segundo = _manager(tmp_path, agora=20.0).add("b")

    assert (primeiro.created_at, segundo.created_at) == (10.0, 20.0)
