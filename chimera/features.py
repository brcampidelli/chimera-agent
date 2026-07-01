"""Optional capabilities catalog — what's built in, and what needs a key or a dep.

Chimera ships these as pre-set slots: add the credential (or install the
dependency) and the capability lights up. ``chimera features`` shows the live
status. ``web_search`` is the reference implementation (key-gated — it
auto-registers the moment ``TAVILY_API_KEY`` is set); the rest follow the same
shape, or plug in via the MCP client / OpenAPI->tool importer that already exist.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from importlib.util import find_spec

from chimera.config import Settings, get_settings


@dataclass(frozen=True)
class Feature:
    name: str
    summary: str
    env_any: tuple[str, ...] = ()  # any one of these credentials satisfies it
    dep: str | None = None  # an importable module that must be installed
    builtin: bool = False  # works out of the box (no key, no dep)
    how: str = ""  # one-line how-to


@dataclass
class FeatureStatus:
    feature: Feature
    has_key: bool
    has_dep: bool

    @property
    def ready(self) -> bool:
        key_ok = self.feature.builtin or not self.feature.env_any or self.has_key
        return key_ok and (self.feature.dep is None or self.has_dep)

    @property
    def blocker(self) -> str:
        if not (self.feature.builtin or not self.feature.env_any or self.has_key):
            return "set " + " or ".join(self.feature.env_any)
        if self.feature.dep and not self.has_dep:
            return f"pip install {self.feature.dep}"
        return ""


CATALOG: tuple[Feature, ...] = (
    Feature("vision", "Image input / paste to a vision model", builtin=True,
            how="chimera run --image <path|url> -m <vision-model>"),
    Feature("deliverable", "Produce polished, self-contained artifacts", builtin=True,
            how="chimera deliver ..."),
    Feature("pet", "Virtual companion", builtin=True, how="chimera pet ..."),
    Feature("web_search", "Search + extract from the web (Tavily reference tool)",
            env_any=("TAVILY_API_KEY",), how="set TAVILY_API_KEY — the web_search tool auto-registers"),
    Feature("code_execution", "Run Python in the sandbox (execute_code tool)", builtin=True,
            how="execute_code is always available (runs via the sandbox)"),
    Feature("arxiv_search", "Search arXiv papers (arxiv_search tool)", builtin=True,
            how="arxiv_search is always available (no key needed)"),
    Feature("youtube_transcript", "Fetch YouTube transcripts (youtube_transcript tool)",
            dep="youtube-transcript-api", how="uv sync --extra youtube"),
    Feature("x_search", "Search X (Twitter)", env_any=("X_BEARER_TOKEN",),
            how="set X_BEARER_TOKEN, then add a tool via the OpenAPI->tool importer"),
    Feature("image_generation", "Generate images (generate_image reference tool)",
            env_any=("OPENAI_API_KEY", "CHIMERA_OPENAI_KEYS"),
            how="set OPENAI_API_KEY — the generate_image tool auto-registers"),
    Feature("tts_voice", "Text-to-speech (text_to_speech reference tool)",
            env_any=("ELEVENLABS_API_KEY",),
            how="set ELEVENLABS_API_KEY — the text_to_speech tool auto-registers"),
    Feature("email", "Send email over SMTP (send_email reference tool)",
            env_any=("CHIMERA_SMTP_HOST",),
            how="set CHIMERA_SMTP_HOST / _USER / _PASSWORD — the send_email tool auto-registers"),
    Feature("spotify", "Control Spotify", env_any=("SPOTIFY_CLIENT_ID",),
            how="set Spotify OAuth creds; import the Spotify OpenAPI spec"),
    Feature("browser", "Browser automation", dep="playwright",
            how="pip install playwright && playwright install"),
    Feature("voice_mode", "Full voice conversation (STT + TTS)",
            env_any=("ELEVENLABS_API_KEY", "OPENAI_API_KEY"), how="set a TTS key; STT via Whisper/API"),
    Feature("computer_use", "Control the desktop (advanced; off the server path)",
            dep="pyautogui", how="pip install pyautogui"),
)


def _dep_present(module: str | None) -> bool:
    if module is None:
        return True
    with contextlib.suppress(ImportError, ValueError):
        return find_spec(module) is not None
    return False


def feature_status(settings: Settings | None = None) -> list[FeatureStatus]:
    """Resolve each catalog feature against the current credentials and deps."""
    creds = (settings or get_settings()).credentials()
    return [
        FeatureStatus(
            feature=feature,
            has_key=any(creds.get(var) for var in feature.env_any),
            has_dep=_dep_present(feature.dep),
        )
        for feature in CATALOG
    ]
