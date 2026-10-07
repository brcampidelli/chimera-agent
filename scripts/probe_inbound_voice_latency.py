"""Measure configured audio transcription latency without installing or downloading a model.

Usage: uv run python scripts/probe_inbound_voice_latency.py path/to/local-audio.ogg
The existing TranscribeAudioTool decides whether a local model is available; this probe never
fetches model weights itself. The file must already exist on the operator's machine.
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

from chimera.api.attachments import transcribe


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audio", type=Path, help="existing local audio file; no download is performed")
    parser.add_argument("--language", default=None, help="optional language code")
    args = parser.parse_args()
    if not args.audio.is_file():
        parser.error(f"audio file does not exist: {args.audio}")
    started = time.perf_counter()
    result = transcribe(args.audio, language=args.language)
    elapsed = time.perf_counter() - started
    print(f"elapsed_seconds={elapsed:.3f}")
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
