"""A share link opens nothing by accident: not through an odd character in the URL, and not after a
downgrade to a version that does not know links expire.

Both found by an adversarial review of study 29, P5.5.

* ``hmac.compare_digest`` refuses a ``str`` holding a character outside ASCII with a TypeError, and
  the store compared the token or id given in a URL or a header straight against its own.
  ``DELETE /api/security/access/links/%C3%A9`` answered the owner with a 500 — and so did
  ``GET /guest/api/session?t=%C3%A9`` for anyone who can reach a guest route, the LAN included.
* Expired links stay in ``code_shares.json`` on purpose (the card lists them as expired), with the
  expiry only in ``expires_at``. The loader before expiry existed (081f057c) builds a link from
  ``token``/``session_id``/``created_at``/``label`` and drops every other key, so after a downgrade
  every expired link would open its conversation again, with no expiry at all. A link with an
  expiry is now filed under a key that loader does not read.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from chimera.api.guest_api import CLOSED
from chimera.api.sharing import EXPIRING_TOKEN, ShareStore
from tests.test_a_conversation_can_be_shared_by_a_token_that_reaches_only_it import _session_of
from tests.test_every_way_into_this_machine_is_on_one_card import _build, _clock

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


# ------------------------------------------------------------------ a downgrade


def _loaded_before_expiry_existed(path: Path) -> list[str]:
    """The tokens the store of 081f057c would load from ``path`` — its filter, word for word: a row
    is a link when it has a ``token`` and a ``session_id``, and every other key is dropped."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    return [
        str(item.get("token") or "")
        for item in (raw if isinstance(raw, list) else [])
        if isinstance(item, dict) and item.get("token") and item.get("session_id")
    ]


def test_a_link_with_an_expiry_is_filed_where_a_version_without_expiry_cannot_load_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = _clock(monkeypatch)
    path = tmp_path / "code_shares.json"
    store = ShareStore(path)
    expiring = store.mint("s1", expires_in=3600)
    forever = store.mint("s1")
    expired = store.mint("s1", expires_in=60)
    now[0] += 61
    assert store.resolve(expired.token) is None
    rows = json.loads(path.read_text(encoding="utf-8"))
    by_token = {row.get("token") or row.get(EXPIRING_TOKEN): row for row in rows}
    assert "token" not in by_token[expiring.token] and "token" not in by_token[expired.token]
    assert by_token[forever.token]["token"] == forever.token

    # The old loader sees only the link that never expires: an expired link cannot come back to
    # life under it, and one that had not expired yet is lost - the safe way round.
    assert _loaded_before_expiry_existed(path) == [forever.token]

    # This version reads all three, each with its expiry.
    again = ShareStore(path)
    assert again.resolve(expiring.token) == expiring
    assert again.resolve(forever.token) == forever
    assert again.resolve(expired.token) is None
    assert {s.token for s in again.all()} == {expiring.token, forever.token, expired.token}


def test_a_link_filed_as_expiring_with_no_readable_time_is_expired_not_never(tmp_path: Path) -> None:
    path = tmp_path / "code_shares.json"
    path.write_text(
        json.dumps([
            {EXPIRING_TOKEN: "hand-edited-expiring-token", "session_id": "s", "created_at": 1.0},
            {EXPIRING_TOKEN: "garbled-time-token-123", "session_id": "s", "expires_at": "soon"},
        ]),
        encoding="utf-8",
    )
    store = ShareStore(path)
    assert store.resolve("hand-edited-expiring-token") is None
    assert store.resolve("garbled-time-token-123") is None
    assert all(share.expired() for share in store.all()) and len(store.all()) == 2
