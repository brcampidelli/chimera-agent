"""Media download — fetch a video or its audio from YouTube and 1000+ other sites.

Chimera has a `youtube_transcript` tool (captions) but couldn't download the actual media. We wrap
**yt-dlp**, not pytube: pytube is single-site and perpetually breaks on YouTube player changes, while
yt-dlp is actively maintained, covers 1000+ sites, and handles the cipher/format/age-gate edge cases.
Opt-in (`media-dl` extra); audio extraction also needs ffmpeg on PATH.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from chimera.tools.base import Tool
from chimera.tools.workspace import resolve_in_workspace

_INSTALL_HINT = (
    "error: media download needs the 'media-dl' extra — install with: "
    "pip install 'chimera-agent[media-dl]' (audio extraction also needs ffmpeg on PATH)"
)


def _ytdlp_download(url: str, outtmpl: str, audio_only: bool) -> dict[str, Any] | None:
    """Download ``url`` to ``outtmpl`` via yt-dlp. Returns info dict, or None if yt-dlp isn't installed."""
    try:
        from yt_dlp import YoutubeDL
    except ImportError:
        return None
    opts: dict[str, Any] = {"outtmpl": outtmpl, "quiet": True, "no_warnings": True, "noprogress": True}
    if audio_only:
        opts["format"] = "bestaudio/best"
        opts["postprocessors"] = [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3"}]
    else:
        opts["format"] = "bestvideo*+bestaudio/best"
    with YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
    return {"title": info.get("title"), "ext": info.get("ext"), "duration": info.get("duration")}


class DownloadMediaTool(Tool):
    name = "download_media"
    description = (
        "Download a video (or just its audio) from YouTube or 1000+ other sites into the workspace. "
        "Args: url; optional audio_only (bool, extract mp3 — needs ffmpeg); out_dir. Returns the saved "
        "file path(s)."
    )
    parameters = {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "The video/media URL."},
            "audio_only": {"type": "boolean", "description": "Extract audio only (mp3). Needs ffmpeg."},
            "out_dir": {"type": "string", "description": "Workspace subfolder to save into (default 'downloads')."},
        },
        "required": ["url"],
    }

    def __init__(self, workspace: Path | None = None) -> None:
        self.workspace = (workspace or Path.cwd()).resolve()

    def run(self, **kwargs: Any) -> str:
        url = str(kwargs.get("url", "")).strip()
        if not url:
            return "error: download_media needs a url"
        audio_only = bool(kwargs.get("audio_only"))
        out_dir = resolve_in_workspace(self.workspace, str(kwargs.get("out_dir") or "downloads"))
        out_dir.mkdir(parents=True, exist_ok=True)
        before = {p.name for p in out_dir.iterdir()}
        try:
            info = _ytdlp_download(url, str(out_dir / "%(title).80s.%(ext)s"), audio_only)
        except Exception as exc:  # noqa: BLE001 — a download failure is a tool error, not a crash
            return f"error: download failed: {exc}"
        if info is None:
            return _INSTALL_HINT
        new_files = sorted(p.name for p in out_dir.iterdir() if p.name not in before)
        listing = ", ".join(str(out_dir / name) for name in new_files) or "(no new file)"
        return f"downloaded {info.get('title')!r} -> {listing}"
