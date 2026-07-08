"""Persistent user profile (M16-B1): the assistant's stable, cacheable preamble.

The "second brain" needs to know who it works for. This profile is the
user-editable, persistent source for the :class:`~chimera.interface.session.ChatSession`
``profile`` slot — name, preferences, projects, recurring contexts.

Cache economics drive one hard requirement: :func:`render_profile` is
**byte-stable** — same profile in, byte-identical string out (sorted lists, no
timestamps, no randomness). A stable preamble is a stable prompt prefix, which
is what makes provider-side prompt caching land (reads at ~0.1x input price).
Volatile material (memory-derived facts) is appended AFTER the stable part, in
a clearly separated section, so it never breaks the cacheable prefix.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from chimera.telemetry import get_logger

_log = get_logger("interface.profile")

PROFILE_FILE = "profile.json"


class UserProfile(BaseModel):
    """What the assistant durably knows about its user."""

    name: str = ""
    preferences: list[str] = Field(default_factory=list)
    """Standing instructions ("answer in PT-BR", "prefer bullet points")."""
    projects: list[str] = Field(default_factory=list)
    """Ongoing projects/areas the user works on."""
    contexts: list[str] = Field(default_factory=list)
    """Recurring contexts ("timezone America/Sao_Paulo", "works on Windows")."""

    def is_empty(self) -> bool:
        return not (self.name or self.preferences or self.projects or self.contexts)

    def add(self, kind: str, value: str) -> bool:
        """Append a fact to a list field (dedup, stripped). False = unknown kind/dup."""
        value = value.strip()
        if not value:
            return False
        target = {
            "preference": self.preferences,
            "preferences": self.preferences,
            "project": self.projects,
            "projects": self.projects,
            "context": self.contexts,
            "contexts": self.contexts,
        }.get(kind.lower())
        if target is None or value in target:
            return False
        target.append(value)
        return True

    def forget(self, value: str) -> bool:
        """Remove a fact from whichever list holds it. False = not found."""
        value = value.strip()
        for bucket in (self.preferences, self.projects, self.contexts):
            if value in bucket:
                bucket.remove(value)
                return True
        return False


def profile_path(home: Path) -> Path:
    return Path(home) / PROFILE_FILE


def load_profile(path: Path) -> UserProfile:
    """Load the profile; a missing or corrupt file yields an empty profile (never crashes)."""
    path = Path(path)
    if not path.exists():
        return UserProfile()
    try:
        return UserProfile.model_validate_json(path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        _log.warning("profile unreadable (%s) — starting empty", exc)
        return UserProfile()


def save_profile(path: Path, profile: UserProfile) -> None:
    """Persist the profile as stable, human-editable JSON (sorted keys, indented)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = profile.model_dump_json(indent=2)
    path.write_text(payload + "\n", encoding="utf-8")


def render_profile(profile: UserProfile, memory_profile: str = "") -> str:
    """The session preamble. STABLE part first (byte-identical for the same profile),
    volatile memory facts after a separator — never inside the cacheable prefix."""
    if profile.is_empty() and not memory_profile:
        return ""
    lines: list[str] = ["## User profile"]
    if profile.name:
        lines.append(f"Name: {profile.name}")
    for label, bucket in (
        ("Preferences", profile.preferences),
        ("Projects", profile.projects),
        ("Contexts", profile.contexts),
    ):
        if bucket:
            lines.append(f"{label}:")
            lines.extend(f"- {item}" for item in sorted(bucket))
    stable = "\n".join(lines) if len(lines) > 1 or not profile.is_empty() else ""
    if memory_profile.strip():
        volatile = "## Recalled facts (volatile)\n" + memory_profile.strip()
        return f"{stable}\n\n{volatile}" if stable else volatile
    return stable
