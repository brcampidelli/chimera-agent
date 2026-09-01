"""Changing embedder made the index return nothing, permanently, without one line in a log.

`embed_missing` only touches `WHERE vector IS NULL`, so vectors written by a previous embedder stayed
forever. `_cosine` returns 0.0 when the two lengths differ, the `score > 0` filter then drops every
one of them, and `stats()` kept counting them as embedded. The index reported healthy and answered
nothing — and `replace_all` deliberately preserves vectors by ident to avoid paying for a re-embed,
so reindexing did not clear them either.

The `meta` table that fixes this already existed, created on every open, with three accessors and
**no callers anywhere in the repository**.

A derived artefact is not migrated, it is rebuilt: there is no conversion from one model's vector
space to another's. Dropping costs one re-embed and says so in the log; keeping costs a silent,
permanent recall failure.
"""

from __future__ import annotations

from pathlib import Path

from chimera.rag.store import Chunk, ChunkStore


def _chunks(n: int = 3) -> list[Chunk]:
    return [
        Chunk(path=f"a{i}.py", symbol=f"f{i}", kind="function", start_line=1, end_line=2,
              text=f"def f{i}(): pass")
        for i in range(n)
    ]


def _embedder(dim: int):
    def embed(textos: list[str]) -> list[list[float]]:
        return [[0.1] * dim for _ in textos]

    return embed


def test_a_new_embedder_rebuilds_the_vectors(tmp_path: Path) -> None:
    """The whole point: the old vectors are gone and the new ones are written."""
    loja = ChunkStore(tmp_path / "i.sqlite3")
    loja.replace_all(_chunks())
    loja.embed_missing(_embedder(4), embedder="antigo")

    reembedados = loja.embed_missing(_embedder(8), embedder="novo")

    assert reembedados == 3
    assert loja.stats()["embedded"] == 3


def test_the_same_embedder_does_not_rebuild(tmp_path: Path) -> None:
    """The guard against a fix that re-embeds the whole corpus on every run — which would cost real
    money on a hosted embedder and make the resumability above worthless."""
    loja = ChunkStore(tmp_path / "i.sqlite3")
    loja.replace_all(_chunks())
    loja.embed_missing(_embedder(4), embedder="mesmo")

    assert loja.embed_missing(_embedder(4), embedder="mesmo") == 0


def test_the_same_name_at_a_different_size_still_rebuilds(tmp_path: Path) -> None:
    """The name alone is not the signature. A provider that changes a model's dimension under the
    same slug is exactly the case nobody would think to look for."""
    loja = ChunkStore(tmp_path / "i.sqlite3")
    loja.replace_all(_chunks())
    loja.embed_missing(_embedder(4), embedder="mesmo-nome")

    assert loja.embed_missing(_embedder(16), embedder="mesmo-nome") == 3


def test_an_index_that_never_recorded_one_is_not_wiped(tmp_path: Path) -> None:
    """An index built before this field existed has vectors and no signature. Treating an unknown
    signature as a mismatch would throw away every existing index on upgrade — the same reasoning
    that makes an absent field mean "before this existed" rather than a value."""
    loja = ChunkStore(tmp_path / "i.sqlite3")
    loja.replace_all(_chunks())
    loja.embed_missing(_embedder(4))  # no name given, as the old caller did

    assert loja.stats()["embedded"] == 3
    assert loja.embed_missing(_embedder(4)) == 0


def test_an_index_built_before_the_field_survives_being_named(tmp_path: Path) -> None:
    """The upgrade case, and the one the test above could not show.

    That one passes no embedder name, so the early return fires on the name and the signature check
    is never reached — a sabotage that removed the "no signature recorded" half went undetected. An
    index built by the previous release has vectors and no signature, and the FIRST run of this
    release names its embedder. Treating that unknown as a mismatch throws away every existing index
    on upgrade, which is the same rule an absent field follows everywhere else here.
    """
    loja = ChunkStore(tmp_path / "i.sqlite3")
    loja.replace_all(_chunks())
    loja.embed_missing(_embedder(4))  # the old caller: no name, so nothing is recorded
    assert loja.get_meta("embedder") == ""

    reembedados = loja.embed_missing(_embedder(4), embedder="agora-com-nome")

    # ZERO re-embeds, not "the vectors are there afterwards". Written the second way first, and it
    # passed under sabotage: wiping the index rebuilds it, so the end state is identical either way
    # and the only difference is the bill. On a hosted embedder over a real corpus that difference
    # is the whole cost of the mistake.
    assert reembedados == 0
    assert loja.stats()["embedded"] == 3


def test_the_signature_is_recorded(tmp_path: Path) -> None:
    loja = ChunkStore(tmp_path / "i.sqlite3")
    loja.replace_all(_chunks())
    loja.embed_missing(_embedder(4), embedder="modelo-x")

    assert loja.get_meta("embedder") == "modelo-x:4"


def test_a_dead_embedder_does_not_wipe_the_index(tmp_path: Path) -> None:
    """The failure this must not turn into a data loss: an embedder that raises leaves the existing
    vectors alone, because the signature is only compared once a real vector has come back."""
    loja = ChunkStore(tmp_path / "i.sqlite3")
    loja.replace_all(_chunks())
    loja.embed_missing(_embedder(4), embedder="bom")

    def morto(_textos: list[str]) -> list[list[float]]:
        raise RuntimeError("provedor fora do ar")

    loja.embed_missing(morto, embedder="outro")

    assert loja.stats()["embedded"] == 3
