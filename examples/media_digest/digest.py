"""Media digest — turn a video/podcast into a summary: download → transcribe → summarize.

    python examples/media_digest/digest.py <url-or-local-file> [--bullets 5]

This composes three of Chimera's built-in pieces directly, so you can see how the toolbox snaps
together programmatically:

  1. `download_media`   (yt-dlp)          — fetch the audio/video (skipped if you pass a local file)
  2. `transcribe_audio` (faster-whisper)  — speech → text
  3. the LLM gateway                       — text → a short bullet summary

Needs the multimedia extras + ffmpeg + one model key:
    pip install 'chimera-agent[full]'      # + ffmpeg on PATH   (see docs/extending.md)

For the fully-agentic version — where the agent picks and chains these tools itself from a plain
sentence — see this folder's README (`chimera solve "download … transcribe … summarize …"`).
"""

from __future__ import annotations

import glob
import os
import sys
from pathlib import Path


def _resolve_media(source: str) -> str | None:
    """Return a local media path — downloading first when `source` is a URL."""
    from chimera.tools import default_registry

    if not source.startswith(("http://", "https://")):
        return source if os.path.exists(source) else None
    reg = default_registry(Path.cwd())
    result = reg.get("download_media").run(url=source)
    print(f"[download] {result}")
    # The tool reports "downloaded '<name>' -> <path>"; fall back to the newest file in downloads/.
    if "-> " in result:
        path = result.split("-> ", 1)[1].strip().strip("'\"")
        if os.path.exists(path):
            return path
    found = sorted(glob.glob("downloads/**/*.*", recursive=True), key=os.path.getmtime)
    return found[-1] if found else None


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    source = argv[0]
    bullets = int(argv[argv.index("--bullets") + 1]) if "--bullets" in argv else 5

    from chimera.providers import LLMGateway
    from chimera.tools import default_registry

    media = _resolve_media(source)
    if not media:
        print(f"error: could not get media from {source!r}")
        return 1

    reg = default_registry(Path.cwd())
    transcript = reg.get("transcribe_audio").run(path=media)
    if transcript.startswith("error:"):
        print(transcript)
        return 1
    print(f"[transcript] {len(transcript)} chars\n{transcript[:400]}"
          + ("…" if len(transcript) > 400 else ""))

    summary = LLMGateway().quick(
        f"Summarize the following transcript in exactly {bullets} concise bullet points. "
        f"Output only the bullets.\n\n{transcript}"
    )
    print("\n--- summary ---\n" + summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
