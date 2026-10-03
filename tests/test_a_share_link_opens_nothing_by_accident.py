"""A share link opens nothing by accident: not through an odd character in the URL.

Found by an adversarial review of study 29, P5.5. ``hmac.compare_digest`` refuses a ``str`` holding a
character outside ASCII with a TypeError, and the store compared the token or id given in a URL or a
header straight against its own. ``DELETE /api/security/access/links/%C3%A9`` answered the owner
with a 500 — and so did ``GET /guest/api/session?t=%C3%A9`` for anyone who can reach a guest route,
the LAN included: a 500 where every other way a link fails answers 401 with one sentence.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from chimera.api.guest_api import CLOSED
from chimera.api.sharing import ShareStore
from tests.test_a_conversation_can_be_shared_by_a_token_that_reaches_only_it import _session_of
from tests.test_every_way_into_this_machine_is_on_one_card import _build

ODD = ["é", "ü-not-a-token", "日本", "\ud800", "a" * 22 + "é"]


@pytest.mark.parametrize("given", ODD)
def test_the_store_answers_an_odd_token_or_id_with_nothing_rather_than_raising(
    tmp_path: Path, given: str
) -> None:
    store = ShareStore(tmp_path / "code_shares.json")
    share = store.mint("s1")
    assert store.resolve(given) is None
    assert store.revoke(given) is False
    assert store.revoke_id(given) is None
    # Nothing was removed on the way, and the real link still opens.
    assert store.resolve(share.token) == share
    assert store.revoke_id(share.id) == share


def test_an_odd_character_in_a_url_is_a_closed_door_on_the_guest_routes_and_a_no_for_the_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _build(tmp_path, monkeypatch)
    sid = _session_of(client, "hello")
    client.post(f"/api/code/sessions/{sid}/share")

    for path in ("/guest/api/session", "/guest/api/presence"):
        refused = client.get(path, params={"t": "é"})
        assert refused.status_code == 401 and refused.json()["detail"] == CLOSED
    # A header is read as Latin-1, so a byte above 0x7f arrives as a non-ASCII character too.
    header = client.get("/guest/api/session", headers={"Authorization": b"Bearer \xe9t\xe9"})
    assert header.status_code == 401

    assert client.delete("/api/security/access/links/%C3%A9").json() == {"ok": False}
    assert client.delete(f"/api/code/sessions/{sid}/shares/%C3%A9").json() == {"ok": False}
    assert len(client.get("/api/security/access").json()["links"]) == 1
