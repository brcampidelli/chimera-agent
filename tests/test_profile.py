"""Tests for the persistent user profile (M16-B1).

The property under test: render_profile is BYTE-STABLE — same profile, byte-identical
preamble across load/save cycles. That stability is what makes provider prompt
caching land on the session prefix.
"""

from __future__ import annotations

from pathlib import Path

from chimera.interface.profile import (
    UserProfile,
    load_profile,
    profile_path,
    render_profile,
    save_profile,
)


def _full() -> UserProfile:
    return UserProfile(
        name="Bruno",
        preferences=["answer in PT-BR", "bullet points"],
        projects=["chimera-agent", "PassaPro"],
        contexts=["Windows", "timezone America/Sao_Paulo"],
    )


def test_round_trip_preserves_profile(tmp_path: Path) -> None:
    path = profile_path(tmp_path)
    save_profile(path, _full())
    assert load_profile(path) == _full()


def test_render_is_byte_stable_across_save_load_cycles(tmp_path: Path) -> None:
    path = profile_path(tmp_path)
    profile = _full()
    first = render_profile(profile)
    for _ in range(3):
        save_profile(path, profile)
        profile = load_profile(path)
    assert render_profile(profile) == first  # byte-identical — the cacheable property


def test_render_sorts_lists_for_stability() -> None:
    a = UserProfile(preferences=["zeta", "alpha"])
    b = UserProfile(preferences=["alpha", "zeta"])
    assert render_profile(a) == render_profile(b)


def test_render_has_no_timestamps_or_volatility() -> None:
    one = render_profile(_full())
    two = render_profile(_full())
    assert one == two
    assert "202" not in one  # no dates leak in


def test_volatile_memory_goes_after_the_stable_prefix() -> None:
    stable_only = render_profile(_full())
    with_memory = render_profile(_full(), memory_profile="- user likes coffee")
    assert with_memory.startswith(stable_only)  # prefix untouched -> cache safe
    assert "## Recalled facts (volatile)" in with_memory
    assert with_memory.index("volatile") > len(stable_only) - 1


def test_empty_profile_renders_empty() -> None:
    assert render_profile(UserProfile()) == ""
    only_memory = render_profile(UserProfile(), memory_profile="- fact")
    assert only_memory.startswith("## Recalled facts (volatile)")


def test_missing_or_corrupt_file_yields_empty_profile(tmp_path: Path) -> None:
    assert load_profile(tmp_path / "nope.json").is_empty()
    bad = tmp_path / "profile.json"
    bad.write_text("{not json", encoding="utf-8")
    assert load_profile(bad).is_empty()


def test_add_and_forget() -> None:
    profile = UserProfile()
    assert profile.add("preference", "dark mode") is True
    assert profile.add("preference", "dark mode") is False  # dedup
    assert profile.add("weird-kind", "x") is False
    assert profile.add("project", "  ") is False  # blank
    assert profile.add("context", "on Windows") is True
    assert profile.forget("dark mode") is True
    assert profile.forget("dark mode") is False
    assert profile.preferences == []
    assert profile.contexts == ["on Windows"]
