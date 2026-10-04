"""The viewer shows a PDF/Word/Excel/PowerPoint file as its text (study 29, P6.3).

"Open beside" on a receipt opens what the turn wrote, and `create_document` writes exactly the
formats the viewer answered "binary or non-text" about. So the read the viewer makes now returns a
document's text, through the same MarkItDown seam `read_document` uses, labelled with its format —
and the label is what keeps the viewer from offering to EDIT it: saving that text would replace the
document with its own text. MarkItDown is the `documents` extra, so these tests drive the seam.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from chimera.api import fs_api
from chimera.api.schemas import FsFileOut


@pytest.fixture
def ws(tmp_path: Path) -> Path:
    (tmp_path / "report.docx").write_bytes(b"PK\x03\x04 not really a zip")
    (tmp_path / "app.py").write_text("print('hi')\n", encoding="utf-8")
    return tmp_path


def test_a_document_is_read_as_its_text_and_labelled_a_preview(
    ws: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[str] = []

    def convert(path: Path) -> str:
        seen.append(Path(path).name)
        return "# Relatório\n\nVendas subiram."

    monkeypatch.setattr(fs_api, "_document_text", convert)
    out = fs_api.read_file(ws, "report.docx")

    assert out == {
        "path": "report.docx", "content": "# Relatório\n\nVendas subiram.", "truncated": False,
        "document": "docx", "note": "",
    }
    assert seen == ["report.docx"]
    assert FsFileOut(**out).document == "docx"


def test_without_the_reader_it_says_why_and_is_still_a_document_not_a_binary(
    ws: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def missing(_path: Path) -> str:
        raise ImportError("No module named 'markitdown'")

    monkeypatch.setattr(fs_api, "_document_text", missing)
    out = fs_api.read_file(ws, "report.docx")
    assert out["document"] == "docx" and out["content"] == ""
    assert "documents" in out["note"] and "binary" not in out["note"]


def test_a_document_that_will_not_convert_is_a_note_not_a_500(
    ws: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(_path: Path) -> str:
        raise ValueError("corrupt zip")

    monkeypatch.setattr(fs_api, "_document_text", broken)
    assert fs_api.read_file(ws, "report.docx")["note"] == "could not be read as a document"


def test_a_document_over_the_preview_ceiling_is_not_converted(
    ws: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def never(_path: Path) -> str:
        raise AssertionError("converted a file over the ceiling")

    monkeypatch.setattr(fs_api, "_document_text", never)
    monkeypatch.setattr(fs_api, "_MAX_DOCUMENT_BYTES", 5)
    assert fs_api.read_file(ws, "report.docx")["note"] == "too large to preview"


def test_a_long_document_is_capped_like_any_text_read(ws: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fs_api, "_document_text", lambda _p: "x" * (fs_api._MAX_READ_CHARS + 10))
    out = fs_api.read_file(ws, "report.docx")
    assert out["truncated"] is True and len(out["content"]) == fs_api._MAX_READ_CHARS


def test_code_is_still_read_as_code_and_names_no_document(ws: Path) -> None:
    out = fs_api.read_file(ws, "app.py")
    assert out["content"] == "print('hi')\n" and "document" not in out
    assert FsFileOut(**out).document == ""


# --- saving: the preview is never written back over the document ----------------------------------


@pytest.mark.parametrize("name", ["report.docx", "Deck.PPTX", "plan.xlsx", "scan.pdf"])
def test_saving_text_over_a_document_is_refused_and_the_document_is_untouched(
    ws: Path, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    # The review's case: the Edit tab showed a .docx's MarkItDown text as editable, a fix and Ctrl+S
    # sent it to PUT /api/fs/file, and the .docx became `# Relatorio\n\ntexto fix`. Refused at the
    # write itself so that no client — Edit, the Code viewer, the bridge's files.write — can do it.
    original = b"PK\x03\x04 the document's own bytes"
    (ws / name).write_bytes(original)
    monkeypatch.setattr(fs_api, "_document_text", lambda _p: "# Relatorio\n\ntexto")
    shown = fs_api.read_file(ws, name)["content"]
    with pytest.raises(fs_api.NotEditableTextError):
        fs_api.write_file(ws, name, shown + " fix")
    assert (ws / name).read_bytes() == original


def test_saving_text_over_an_existing_binary_file_is_refused(ws: Path) -> None:
    # The viewer shows a non-UTF-8 file as empty with a note; a save would have replaced it with the
    # draft, writing plain \n. Creating a NEW file and saving an existing UTF-8 one still work.
    (ws / "blob.bin").write_bytes(b"\xff\xfe\x00\x01binary\x00")
    with pytest.raises(fs_api.NotEditableTextError):
        fs_api.write_file(ws, "blob.bin", "typed over it")
    assert (ws / "blob.bin").read_bytes() == b"\xff\xfe\x00\x01binary\x00"
    fs_api.write_file(ws, "app.py", "print('ok')\n")
    assert (ws / "app.py").read_text(encoding="utf-8").strip() == "print('ok')"
    assert fs_api.write_file(ws, "notes/new.md", "# novo\n")["path"] == "notes/new.md"


def test_the_save_endpoint_answers_400_for_a_document_and_leaves_it_alone(ws: Path, tmp_path: Path) -> None:
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from chimera.api import build_api_app
    from chimera.config import Settings
    from chimera.interface import ChatSession

    def no_session() -> ChatSession:
        raise AssertionError("a file save must not start a chat session")

    (ws / "r.docx").write_bytes(b"PK\x03\x04 bytes")
    home = tmp_path.parent / f"{tmp_path.name}-home"
    client = TestClient(build_api_app(no_session, settings=Settings(CHIMERA_HOME=str(home)), workspace=ws))
    resp = client.put("/api/fs/file", json={"path": "r.docx", "content": "# Relatorio fix"})
    assert resp.status_code == 400 and resp.json()["detail"] == "not an editable text file"
    assert (ws / "r.docx").read_bytes() == b"PK\x03\x04 bytes"
