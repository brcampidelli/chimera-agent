# Example: media digest — video/podcast → transcript → summary

Point Chimera at a video or podcast URL and get a short summary back. It **downloads** the media,
**transcribes** the speech locally, and **summarizes** the transcript — a real multimodal pipeline
built from tools that ship in the box.

## What it needs

```bash
pip install 'chimera-agent[full]'      # yt-dlp (download) + faster-whisper (transcribe)
# plus ffmpeg on your system, and one model key (chimera doctor to check):
#   macOS: brew install ffmpeg  ·  Ubuntu/Debian: sudo apt install ffmpeg  ·  Windows: choco install ffmpeg
```

## Two ways to run it

**1. The script** ([`digest.py`](digest.py)) — composes the tools explicitly, so you can see how
they snap together (`download_media` → `transcribe_audio` → an LLM summary):

```bash
python examples/media_digest/digest.py "https://example.com/talk.mp4" --bullets 5
# works on a local file too (skips the download):
python examples/media_digest/digest.py ./meeting.mp3
```

**2. Let the agent do it** — hand the whole pipeline to the autonomous loop in one sentence; the
agent picks and chains the tools itself (this is `chimera solve`, which runs the real tool loop —
note `chimera run` is a single completion with no tools):

```bash
chimera solve "download the audio from https://example.com/talk.mp4, transcribe it, and write a \
5-bullet summary to digest.md" -w ./scratch --verify "test -s digest.md"
```

The `--verify` makes it keep the result only if `digest.md` actually got written — verify-or-revert,
so a silent failure is retried, not passed off.

## Make it a daily habit

Wrap either form in `chimera cron` to get, say, a morning digest of a podcast feed:

```bash
chimera cron add podcast "0 7 * * *" "python examples/media_digest/digest.py <today-url>"
```

## Notes

- **Fully local transcription:** `transcribe_audio` uses faster-whisper on your machine — the audio
  never leaves it. (An OpenAI key enables the API path instead, if you prefer.)
- **YouTube & 1000+ sites:** `download_media` is yt-dlp under the hood. Some hosts rate-limit
  datacenter IPs; a direct media URL is the most reliable.
- **Safety:** downloaded content is untrusted, so under `--taint` it's tracked and a later
  exec/read on it escalates for approval (see [docs/security.md](../../docs/security.md)).
