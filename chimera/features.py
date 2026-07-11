"""Optional capabilities catalog — what's built in, and what needs a key or a dep.

Chimera ships these as pre-set slots: add the credential (or install the
dependency) and the capability lights up. ``chimera features`` shows the live
status. ``web_search`` is the reference implementation (key-gated — it
auto-registers the moment ``TAVILY_API_KEY`` is set); the rest follow the same
shape, or plug in via the MCP client / OpenAPI->tool importer that already exist.
"""

from __future__ import annotations

import contextlib
import shutil
from dataclasses import dataclass
from importlib.util import find_spec

from chimera.config import Settings, get_settings


@dataclass(frozen=True)
class Feature:
    name: str
    summary: str
    env_any: tuple[str, ...] = ()  # any one of these credentials satisfies it
    dep: str | None = None  # an IMPORTABLE module name that must be installed (e.g. "yt_dlp")
    extra: str | None = None  # the chimera-agent extra that provides `dep` (nicer install hint)
    bin: str | None = None  # a system binary that must be on PATH (e.g. "ffmpeg")
    builtin: bool = False  # works out of the box (no key, no dep)
    how: str = ""  # one-line how-to


@dataclass
class FeatureStatus:
    feature: Feature
    has_key: bool
    has_dep: bool
    has_bin: bool = True

    @property
    def ready(self) -> bool:
        key_ok = self.feature.builtin or not self.feature.env_any or self.has_key
        dep_ok = self.feature.dep is None or self.has_dep
        bin_ok = self.feature.bin is None or self.has_bin
        return key_ok and dep_ok and bin_ok

    @property
    def blocker(self) -> str:
        if not (self.feature.builtin or not self.feature.env_any or self.has_key):
            return "set " + " or ".join(self.feature.env_any)
        if self.feature.dep and not self.has_dep:
            # Prefer the friendly extra-based install (pip install 'chimera-agent[documents]')
            # over the bare module name, so the hint actually works copy-pasted.
            return (
                f"pip install 'chimera-agent[{self.feature.extra}]'"
                if self.feature.extra
                else f"pip install {self.feature.dep}"
            )
        if self.feature.bin and not self.has_bin:
            return f"install {self.feature.bin} (e.g. apt install {self.feature.bin})"
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
    Feature("code_interpreter", "Stateful Python session (code_interpreter tool)", builtin=True,
            how="code_interpreter is always available (in-process; state persists)"),
    Feature("read_email", "Read email over IMAP (read_email tool)", env_any=("CHIMERA_IMAP_HOST",),
            how="set CHIMERA_IMAP_HOST / _USER / _PASSWORD — the read_email tool auto-registers"),
    Feature("calendar", "List calendar events (calendar_events tool)", env_any=("CHIMERA_CALENDAR_ICS_URL",),
            how="set CHIMERA_CALENDAR_ICS_URL (or pass a url) — the calendar_events tool auto-registers"),
    Feature("arxiv_search", "Search arXiv papers (arxiv_search tool)", builtin=True,
            how="arxiv_search is always available (no key needed)"),
    Feature("youtube_transcript", "Fetch YouTube transcripts (youtube_transcript tool)",
            dep="youtube_transcript_api", extra="youtube",
            how="pip install 'chimera-agent[youtube]'"),
    Feature("documents", "Read PDF/Word/Excel/… as text (read_document tool via MarkItDown)",
            dep="markitdown", extra="documents",
            how="pip install 'chimera-agent[documents]' — then: chimera run \"summarize report.pdf\""),
    Feature("media_download", "Download video/audio from YouTube + 1000+ sites (download_media tool)",
            dep="yt_dlp", extra="media-dl", bin="ffmpeg",
            how="pip install 'chimera-agent[media-dl]' + ffmpeg — then use the download_media tool"),
    Feature("speech_to_text", "Transcribe audio locally (transcribe_audio tool via faster-whisper)",
            dep="faster_whisper", extra="stt", bin="ffmpeg",
            how="pip install 'chimera-agent[stt]' + ffmpeg (or set an OpenAI key to use the API)"),
    Feature("data_analysis", "Analyze data with pandas/scikit-learn (data_analysis skill)",
            dep="pandas", extra="data",
            how="pip install 'chimera-agent[data]' — the data_analysis skill runs real pandas code"),
    Feature("charts", "Make charts with matplotlib/seaborn/plotly (data_visualization skill)",
            dep="matplotlib", extra="viz",
            how="pip install 'chimera-agent[viz]' — the data_visualization skill renders charts"),
    Feature("x_search", "Search X (Twitter) — pluggable via the OpenAPI->tool importer (no built-in tool)",
            env_any=("X_BEARER_TOKEN",),
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
    Feature("spotify", "Control Spotify — pluggable via the OpenAPI->tool importer (no built-in tool)",
            env_any=("SPOTIFY_CLIENT_ID",),
            how="set Spotify OAuth creds; import the Spotify OpenAPI spec"),
    Feature("browser", "Browser automation — read/scrape rendered pages (browser tool)", builtin=True,
            how="works out of the box (Chromium auto-installs on first use)"),
)


def _dep_present(module: str | None) -> bool:
    if module is None:
        return True
    with contextlib.suppress(ImportError, ValueError):
        return find_spec(module) is not None
    return False


def _bin_present(binary: str | None) -> bool:
    return binary is None or shutil.which(binary) is not None


def feature_status(settings: Settings | None = None) -> list[FeatureStatus]:
    """Resolve each catalog feature against the current credentials, deps, and system binaries."""
    creds = (settings or get_settings()).credentials()
    return [
        FeatureStatus(
            feature=feature,
            has_key=any(creds.get(var) for var in feature.env_any),
            has_dep=_dep_present(feature.dep),
            has_bin=_bin_present(feature.bin),
        )
        for feature in CATALOG
    ]
