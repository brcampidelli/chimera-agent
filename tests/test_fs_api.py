"""Tests for the read-only filesystem helpers behind the Code screen (tree + file viewer)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from chimera.api.fs_api import (
    ImageTooLargeError,
    UnsupportedImageError,
    UnsupportedMediaError,
    list_tree,
    read_file,
    read_image,
    read_media,
    write_file,
)
from chimera.tools.workspace import PathEscapesWorkspaceError, ProtectedPathError

#: The smallest thing a browser accepts as a PNG — an 8-byte signature is enough for these, which
#: are about which bytes come back, not about decoding them.
PNG = b"\x89PNG\r\n\x1a\n"


def _workspace(tmp_path: Path) -> Path:
    (tmp_path / "src").mkdir()
    (tmp_path / ".git").mkdir()  # pruned
    (tmp_path / "src" / "app.py").write_text("print('hi')\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("# hello\n", encoding="utf-8")
    return tmp_path


def test_tree_lists_immediate_children_dirs_first_and_prunes_ignored(tmp_path: Path) -> None:
    tree = list_tree(_workspace(tmp_path), "")
    names = [e["name"] for e in tree["entries"]]
    assert ".git" not in names  # ignored dir pruned
    assert names == ["src", "README.md"]  # dirs first, then files, alphabetical
    src = next(e for e in tree["entries"] if e["name"] == "src")
    assert src["is_dir"] is True and src["path"] == "src"
    assert tree["capped"] is False


def test_tree_of_a_subdir_returns_its_children(tmp_path: Path) -> None:
    tree = list_tree(_workspace(tmp_path), "src")
    assert [e["path"] for e in tree["entries"]] == ["src/app.py"]


def test_tree_caps_at_max_entries(tmp_path: Path) -> None:
    for i in range(10):
        (tmp_path / f"f{i}.txt").write_text("x", encoding="utf-8")
    tree = list_tree(tmp_path, "", max_entries=4)
    assert len(tree["entries"]) == 4
    assert tree["capped"] is True


def test_read_file_returns_content_and_no_note(tmp_path: Path) -> None:
    out = read_file(_workspace(tmp_path), "src/app.py")
    assert out["content"] == "print('hi')\n"
    assert out["truncated"] is False and out["note"] == ""


def test_read_file_truncates_over_the_cap(tmp_path: Path) -> None:
    big = "a" * 25_000
    (tmp_path / "big.txt").write_text(big, encoding="utf-8")
    out = read_file(tmp_path, "big.txt")
    assert out["truncated"] is True
    assert len(out["content"]) == 20_000


def test_read_a_directory_returns_a_note_not_a_crash(tmp_path: Path) -> None:
    out = read_file(_workspace(tmp_path), "src")
    assert out["content"] == "" and out["note"] == "binary or non-text"


def test_read_a_binary_file_returns_a_note(tmp_path: Path) -> None:
    (tmp_path / "blob.bin").write_bytes(b"\xff\xfe\x00\x01binary\x00")
    out = read_file(tmp_path, "blob.bin")
    assert out["content"] == "" and out["note"] == "binary or non-text"


def test_read_a_missing_file_returns_not_found(tmp_path: Path) -> None:
    out = read_file(tmp_path, "nope.txt")
    assert out["note"] == "not found"


def test_path_escape_raises_for_tree_and_file(tmp_path: Path) -> None:
    with pytest.raises(PathEscapesWorkspaceError):
        list_tree(tmp_path, "../..")
    with pytest.raises(PathEscapesWorkspaceError):
        read_file(tmp_path, "../secret.txt")


# --- write_file (editable viewer save) ------------------------------------------------------------


def test_write_creates_a_new_file_and_parent_dirs(tmp_path: Path) -> None:
    out = write_file(tmp_path, "pkg/sub/new.txt", "hello\nworld\n")
    assert out == {"path": "pkg/sub/new.txt", "bytes": 12}
    written = tmp_path / "pkg" / "sub" / "new.txt"
    assert written.read_bytes() == b"hello\nworld\n"  # new file gets plain \n


def test_write_preserves_crlf_on_an_existing_crlf_file(tmp_path: Path) -> None:
    # The editor loads content \n-normalized (as read_file returns it); saving it back must restore
    # the file's own CRLF convention, not flip untouched lines to the platform ending.
    target = tmp_path / "win.txt"
    target.write_bytes(b"a\r\nb\r\n")
    out = write_file(tmp_path, "win.txt", "a\nb\nc\n")
    assert target.read_bytes() == b"a\r\nb\r\nc\r\n"
    assert out["bytes"] == 9  # 3 lines × ("x" + "\r\n") == 9 bytes on disk


def test_write_rejects_a_path_escape(tmp_path: Path) -> None:
    with pytest.raises(PathEscapesWorkspaceError):
        write_file(tmp_path, "../evil.txt", "x")


def test_write_rejects_oversize_content(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        write_file(tmp_path, "big.txt", "a" * 50, max_bytes=10)
    assert not (tmp_path / "big.txt").exists()  # rejected before any write


# --- read_image (the byte endpoint behind the inline preview) --------------------------------------


def test_an_image_comes_back_as_its_own_bytes_and_an_image_type(tmp_path: Path) -> None:
    (tmp_path / "chart.png").write_bytes(PNG)
    assert read_image(tmp_path, "chart.png") == (PNG, "image/png")


def test_the_type_is_chosen_by_extension_across_the_allowlist(tmp_path: Path) -> None:
    for name, expected in [("a.jpg", "image/jpeg"), ("b.JPEG", "image/jpeg"), ("c.gif", "image/gif")]:
        (tmp_path / name).write_bytes(PNG)
        assert read_image(tmp_path, name)[1] == expected


def test_html_in_the_workspace_is_refused_rather_than_served(tmp_path: Path) -> None:
    """The whole reason this is an image reader and not a byte reader.

    The page carries the bearer token in a `<meta>` tag, so a document served from this origin can
    fetch index.html, read the token out of it, and then drive the API as the user. A workspace is
    exactly where such a file could appear — the agent writes files there, and a fetched page that
    talked it into writing one would have planted it.
    """
    (tmp_path / "steal.html").write_bytes(b"<script>fetch('/')</script>")
    with pytest.raises(UnsupportedImageError):
        read_image(tmp_path, "steal.html")


def test_a_valid_svg_is_served_as_its_validated_rebuild(tmp_path: Path) -> None:
    """SVG is served only after the validator accepts it, and what comes back is the rebuild."""
    (tmp_path / "chart.svg").write_text(
        "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 2 2'><!-- c --><rect width='1' height='1'/></svg>",
        encoding="utf-8",
    )
    data, media_type = read_image(tmp_path, "chart.svg")
    assert media_type == "image/svg+xml"
    assert b"<rect" in data and b"<!--" not in data


def test_an_svg_that_fails_validation_is_refused(tmp_path: Path) -> None:
    """Deliberate: an SVG runs its script in a top-level navigation, so a file the validator refuses
    is not served. The text viewer still shows its source, which is the owner's own file."""
    (tmp_path / "chart.svg").write_text("<svg xmlns='http://www.w3.org/2000/svg'><script>x</script></svg>", encoding="utf-8")
    with pytest.raises(UnsupportedImageError):
        read_image(tmp_path, "chart.svg")
    assert read_file(tmp_path, "chart.svg")["content"].startswith("<svg")  # still readable as text


def test_an_extension_that_only_looks_like_an_image_is_refused(tmp_path: Path) -> None:
    # `.png.html` and a bare `.html` are the same refusal; `evil.html.png` is on the allowlist and
    # IS served as image/png, which is safe only because the endpoint sends `nosniff` — asserted
    # over in the endpoint test, since a header is not this function's to send.
    (tmp_path / "evil.png.html").write_bytes(b"<html>")
    with pytest.raises(UnsupportedImageError):
        read_image(tmp_path, "evil.png.html")


def test_reading_an_image_uses_the_same_path_guard_as_the_text_read(tmp_path: Path) -> None:
    with pytest.raises(PathEscapesWorkspaceError):
        read_image(tmp_path, "../secret.png")


def test_a_missing_image_raises_rather_than_returning_empty_bytes(tmp_path: Path) -> None:
    # The text read returns a note here. Bytes have nowhere to put one, so this raises and the
    # endpoint answers 404 — an empty 200 would render as a broken image with no reason given.
    with pytest.raises(FileNotFoundError):
        read_image(tmp_path, "nope.png")


def test_a_directory_named_like_an_image_is_not_a_file(tmp_path: Path) -> None:
    (tmp_path / "assets.png").mkdir()
    with pytest.raises(FileNotFoundError):
        read_image(tmp_path, "assets.png")


def test_an_oversize_image_is_refused_before_it_is_read(tmp_path: Path) -> None:
    (tmp_path / "huge.png").write_bytes(PNG * 10)
    with pytest.raises(ImageTooLargeError):
        read_image(tmp_path, "huge.png", max_bytes=16)


# --- read_media (the byte endpoint behind the inline audio/video player) ----------------------------


def test_media_comes_back_as_its_own_bytes_and_a_media_type(tmp_path: Path) -> None:
    (tmp_path / "site").mkdir()
    (tmp_path / "site" / "narracao.mp3").write_bytes(b"ID3payload")
    assert read_media(tmp_path, "site/narracao.mp3") == (b"ID3payload", "audio/mpeg")


def test_the_media_type_is_chosen_by_extension_across_the_allowlist(tmp_path: Path) -> None:
    expected = {
        "a.wav": "audio/wav",
        "b.OGG": "audio/ogg",
        "c.m4a": "audio/mp4",
        "d.mp4": "video/mp4",
        "e.webm": "video/webm",
    }
    for name, media_type in expected.items():
        (tmp_path / name).write_bytes(b"x")
        assert read_media(tmp_path, name)[1] == media_type


def test_a_document_or_an_image_is_not_served_as_media(tmp_path: Path) -> None:
    # An image has its own endpoint; an HTML file must never get a media label from here.
    (tmp_path / "steal.html").write_text("<script>fetch('/')</script>", encoding="utf-8")
    (tmp_path / "chart.png").write_bytes(PNG)
    with pytest.raises(UnsupportedMediaError):
        read_media(tmp_path, "steal.html")
    with pytest.raises(UnsupportedMediaError):
        read_media(tmp_path, "chart.png")


def test_reading_media_uses_the_same_path_guard_as_the_text_read(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    outside = tmp_path / "outside.mp3"
    outside.write_bytes(b"x")
    with pytest.raises(PathEscapesWorkspaceError):
        read_media(ws, "../outside.mp3")
    with pytest.raises(PathEscapesWorkspaceError):
        read_media(ws, str(outside))  # an absolute path to a real file outside


def test_a_symlink_out_of_the_workspace_is_refused(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    outside = tmp_path / "outside.mp3"
    outside.write_bytes(b"x")
    try:
        (ws / "link.mp3").symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("this platform does not let this account create symlinks")
    with pytest.raises(PathEscapesWorkspaceError):
        read_media(ws, "link.mp3")


def test_a_hard_link_to_a_file_outside_is_refused(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    outside = tmp_path / "outside.mp3"
    outside.write_bytes(b"x")
    try:
        os.link(outside, ws / "hard.mp3")
    except OSError:
        pytest.skip("this platform does not allow hard links here")
    # Its path is inside the workspace, so the resolve passes; the second name is what gives it away.
    with pytest.raises(PathEscapesWorkspaceError):
        read_media(ws, "hard.mp3")


def test_chimera_env_and_its_data_folder_are_refused_to_media_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = tmp_path / "proj"
    home = tmp_path / "home"
    ws.mkdir()
    home.mkdir()
    env = ws / ".env"
    env.write_text("KEY=1", encoding="utf-8")
    monkeypatch.setattr("chimera.core.own_files.own_env_files", lambda: [env])
    with pytest.raises(ProtectedPathError):
        read_media(ws, ".env")
    monkeypatch.setattr("chimera.tools.workspace.chimera_home", lambda: home)
    (home / "approval.mp3").write_bytes(b"x")
    with pytest.raises(ProtectedPathError):
        read_media(home, "approval.mp3")


@pytest.mark.parametrize(
    "rel",
    [
        "\\\\attacker\\share\\a.mp3",  # UNC: the NTLM hash goes to the server named
        "\\\\?\\C:\\a.mp3",  # extended-length prefix
        "C:\\a.mp3",  # drive letter
        "C:a.mp3",  # drive-relative
        "\\a.mp3",  # rooted on the current drive
        "/etc/a.mp3",  # absolute, POSIX
    ],
)
def test_a_network_or_rooted_path_is_refused_on_both_media_and_image_reads(tmp_path: Path, rel: str) -> None:
    with pytest.raises(PathEscapesWorkspaceError):
        read_media(tmp_path, rel)
    with pytest.raises(PathEscapesWorkspaceError):
        read_image(tmp_path, rel)


def test_a_unc_path_is_refused_before_anything_is_resolved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The point of the refusal: no `resolve` may reach the share, not even the workspace's own.
    def no_resolve(self: Path, *args: object, **kwargs: object) -> Path:
        raise AssertionError(f"resolve() was called on {self!s}")

    monkeypatch.setattr(Path, "resolve", no_resolve)
    with pytest.raises(PathEscapesWorkspaceError):
        read_media(tmp_path, "\\\\attacker\\share\\a.mp3")


def test_a_missing_media_file_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        read_media(tmp_path, "nope.mp3")


def test_an_oversize_media_file_is_refused_before_it_is_read(tmp_path: Path) -> None:
    (tmp_path / "long.mp4").write_bytes(b"0123456789abcdef")
    with pytest.raises(ImageTooLargeError):
        read_media(tmp_path, "long.mp4", max_bytes=8)
