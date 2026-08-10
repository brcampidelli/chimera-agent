"""Rotation pools, edited by operation instead of by value.

The pools have worked for a long time and no interface could reach them, so the obvious fix was to
put the CSV in a text field. That fix has a failure mode worth naming: the field would have to
display the current value to be editable, the display has to be masked, and a client that re-submits
what it displayed writes `…abcd` over a working rotation — in the `.env` AND in `os.environ`, so the
gateway starts rotating over a mask for the rest of the session.

Add takes one key and never the list; remove takes a position and never a value. There is no request
shape that carries a key back to the server, which is what makes the failure inexpressible rather
than merely unlikely.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from chimera.api.config_api import is_editable, patch_config, pool_add, pool_remove, read_pools
from chimera.config import Settings, get_settings


def _pool(monkeypatch: Any, keys: str) -> None:
    monkeypatch.setenv("CHIMERA_OPENROUTER_KEYS", keys)
    get_settings.cache_clear()


class TestReading:
    def test_only_position_and_last_four_ever_leave_the_server(self, monkeypatch: Any) -> None:
        _pool(monkeypatch, "sk-or-aaaa1111,sk-or-bbbb2222")
        pools = {p["provider"]: p for p in read_pools(get_settings())}

        assert pools["openrouter"]["keys"] == [
            {"index": 0, "hint": "…1111"},
            {"index": 1, "hint": "…2222"},
        ]
        assert "sk-or-aaaa1111" not in str(pools)

    def test_every_first_class_provider_has_a_pool_entry(self) -> None:
        # An empty list, not a missing key: the screen needs somewhere to put the first one.
        pools = {p["provider"]: p for p in read_pools(Settings())}
        assert set(pools) == {"openrouter", "openai", "anthropic", "gemini", "deepseek"}
        assert pools["gemini"] == {"provider": "gemini", "env": "CHIMERA_GEMINI_KEYS", "keys": []}


class TestWriting:
    def test_adding_a_key_keeps_the_ones_the_client_never_saw(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        _pool(monkeypatch, "sk-or-aaaa1111")
        out = pool_add("openrouter", "sk-or-bbbb2222", env_path=tmp_path / ".env")

        assert out == {"provider": "openrouter", "count": 2}
        assert get_settings().credential_pool("openrouter") == ["sk-or-aaaa1111", "sk-or-bbbb2222"]

    def test_removing_by_index_leaves_the_rest_intact(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        _pool(monkeypatch, "a-1111,b-2222,c-3333")
        pool_remove("openrouter", 1, env_path=tmp_path / ".env")
        assert get_settings().credential_pool("openrouter") == ["a-1111", "c-3333"]

    def test_the_change_reaches_the_env_file_and_the_live_process(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # Both, because the gateway reads the environment and a restart must not undo the edit.
        _pool(monkeypatch, "")
        env_file = tmp_path / ".env"
        pool_add("openrouter", "sk-or-written", env_path=env_file)

        import os

        assert "CHIMERA_OPENROUTER_KEYS=sk-or-written" in env_file.read_text(encoding="utf-8")
        assert os.environ["CHIMERA_OPENROUTER_KEYS"] == "sk-or-written"


class TestRefusals:
    def test_the_mask_is_not_accepted_as_a_key(self, tmp_path: Path, monkeypatch: Any) -> None:
        # The bug this whole shape exists to prevent, caught even so.
        _pool(monkeypatch, "sk-or-aaaa1111")
        with pytest.raises(ValueError, match="masked hint"):
            pool_add("openrouter", "…1111", env_path=tmp_path / ".env")
        assert get_settings().credential_pool("openrouter") == ["sk-or-aaaa1111"]

    def test_a_comma_is_refused_because_it_is_the_separator(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="comma"):
            pool_add("openrouter", "key-one,key-two", env_path=tmp_path / ".env")

    def test_a_newline_cannot_smuggle_a_second_env_line(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="newline"):
            pool_add("openrouter", "k\nCHIMERA_SANDBOX=local", env_path=tmp_path / ".env")

    def test_an_empty_key_is_not_a_way_to_clear_the_pool(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="empty"):
            pool_add("openrouter", "   ", env_path=tmp_path / ".env")

    def test_a_duplicate_is_refused_rather_than_rotated_onto_twice(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        _pool(monkeypatch, "sk-or-aaaa1111")
        with pytest.raises(ValueError, match="already"):
            pool_add("openrouter", "sk-or-aaaa1111", env_path=tmp_path / ".env")

    def test_an_index_outside_the_pool_is_refused(self, tmp_path: Path, monkeypatch: Any) -> None:
        _pool(monkeypatch, "only-1111")
        with pytest.raises(ValueError, match="no key at index"):
            pool_remove("openrouter", 3, env_path=tmp_path / ".env")

    def test_an_unknown_provider_is_named_rather_than_silently_ignored(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="unknown provider"):
            pool_add("groq", "gsk-x", env_path=tmp_path / ".env")

    def test_the_pool_variable_stays_out_of_the_string_writing_endpoint(self) -> None:
        # The other half of the guarantee: if PATCH could write CHIMERA_*_KEYS, every refusal above
        # would have a way around it.
        assert is_editable("CHIMERA_OPENROUTER_KEYS") is False
        with pytest.raises(ValueError, match="not editable"):
            patch_config({"CHIMERA_OPENROUTER_KEYS": "…abcd"})
