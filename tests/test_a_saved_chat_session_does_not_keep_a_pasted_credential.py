from __future__ import annotations

import json
from pathlib import Path

import pytest

from chimera.api.sessions import SessionStore
from chimera.interface.session import ChatTurn


def test_a_credential_pasted_into_the_chat_is_masked_on_disk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Built by concatenation so the secret scanner does not read the test file as a leak.
    secret = "tok-" + "pasted-by-the-owner-0123456789"
    monkeypatch.setenv("MY_SERVICE_API_KEY", secret)
    store = SessionStore(tmp_path)

    store.save("s1", [ChatTurn(user=f"my key is {secret}", assistant=f"I will not repeat {secret}")])

    raw = (tmp_path / "s1.json").read_text(encoding="utf-8")
    assert secret not in raw
    json.loads(raw)  # masking must leave the file valid JSON
    reopened = SessionStore(tmp_path).load("s1")
    assert reopened[0].user == "my key is [redacted]"
    assert reopened[0].assistant == "I will not repeat [redacted]"


def test_the_title_is_masked_before_it_is_cut(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    secret = "tok-" + "title-credential-0123456789abcdef"
    monkeypatch.setenv("MY_SERVICE_API_KEY", secret)
    store = SessionStore(tmp_path)
    # The secret starts at character 50: a cut at 60 before masking would keep ten of its characters.
    store.save("s2", [ChatTurn(user="please remember this one: " + "x" * 25 + secret, assistant="ok")])

    meta = SessionStore(tmp_path).list()[0]
    assert secret[:8] not in meta.title


def test_a_value_is_masked_whatever_the_suffix_of_its_variable_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # `_KEY` is not among the old markers (`API_KEY`, `TOKEN`, ...), only among the env-scrub suffixes.
    secret = "opaque_" + "0123456789abcdef"
    monkeypatch.setenv("STRIPE_KEY", secret)
    store = SessionStore(tmp_path)

    store.save("s3", [ChatTurn(user=f"the key is {secret}", assistant="noted")])

    assert secret not in (tmp_path / "s3.json").read_text(encoding="utf-8")


def test_the_history_index_masks_before_it_cuts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from chimera.memory.history import TEXT_CAP, HistoryIndex

    secret = "tok-" + "history-credential-0123456789abcdef"
    monkeypatch.setenv("MY_SERVICE_API_KEY", secret)
    index = HistoryIndex(tmp_path)
    # Cut at TEXT_CAP before masking, the secret would keep its first characters in the index.
    index.record(
        turn_id="t1", session_id="s", project=None,
        asked="q" * (TEXT_CAP - 10) + secret, answered="ok",
    )

    stored = index.recent(k=1)[0].asked
    assert secret[:6] not in stored
    index.close()
