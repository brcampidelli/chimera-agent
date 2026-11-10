"""The first voice note downloads the speech model, and a failed download is not tried again per note.

Measured before this change: `whisper_snapshot` called `snapshot_download` for every voice note, so a
download that failed was repeated for each one, and a stranger sending notes repeated the traffic.
The download is now announced in the log before it starts, and a failure is refused for a few minutes.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from chimera.server import voice


@pytest.fixture(autouse=True)
def _clean_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(voice, "_DOWNLOAD_FAILED_AT", {})


def test_a_failed_download_is_refused_without_a_second_attempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[dict[str, Any]] = []

    def failing_download(**kwargs: Any) -> str:
        calls.append(kwargs)
        raise OSError("network down")

    monkeypatch.setattr("huggingface_hub.snapshot_download", failing_download)

    with pytest.raises(voice.VoiceUnavailable):
        voice.whisper_snapshot(tmp_path, "base")
    downloads_after_first = len([c for c in calls if not c.get("local_files_only")])

    with pytest.raises(voice.VoiceUnavailable, match="try again in a few minutes"):
        voice.whisper_snapshot(tmp_path, "base")

    assert downloads_after_first == 1
    assert len([c for c in calls if not c.get("local_files_only")]) == 1, (
        "the second note must not start another download"
    )


def test_the_download_is_announced_in_the_log_before_it_starts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    order: list[str] = []

    def fake_download(**kwargs: Any) -> str:
        if kwargs.get("local_files_only"):
            raise OSError("not cached")
        order.append("download")
        return str(tmp_path / "snap")

    monkeypatch.setattr("huggingface_hub.snapshot_download", fake_download)
    with caplog.at_level("WARNING"):
        original_warning = voice._log.warning

        def spy(msg: str, *args: Any, **kwargs: Any) -> None:
            order.append("log")
            original_warning(msg, *args, **kwargs)

        monkeypatch.setattr(voice._log, "warning", spy)
        assert voice.whisper_snapshot(tmp_path, "base") == str(tmp_path / "snap")

    assert order == ["log", "download"], "the owner is told before the download starts"
