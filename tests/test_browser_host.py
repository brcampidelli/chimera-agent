from __future__ import annotations

import io
import json
import struct
from pathlib import Path

import pytest

from chimera.browser_host import (
    MAX_INPUT_MESSAGE_BYTES,
    MAX_MESSAGE_BYTES,
    read_message,
    serve,
    write_message,
)


def _frame(value: object) -> bytes:
    payload = json.dumps(value, ensure_ascii=False).encode("utf-8")
    return struct.pack("<I", len(payload)) + payload


def test_native_message_round_trip_utf8() -> None:
    stream = io.BytesIO()
    write_message(stream, {"id": 4, "text": "olá"})
    stream.seek(0)
    assert read_message(stream) == {"id": 4, "text": "olá"}
    assert read_message(stream) is None


def test_read_rejects_bad_or_non_object_frames() -> None:
    with pytest.raises(ValueError, match="header"):
        read_message(io.BytesIO(b"\x01"))
    with pytest.raises(ValueError, match="payload"):
        read_message(io.BytesIO(struct.pack("<I", 4) + b"{}"))
    with pytest.raises(ValueError, match="object"):
        read_message(io.BytesIO(_frame([])))


def test_read_rejects_oversized_length_before_payload() -> None:
    payload = b"x" * (MAX_INPUT_MESSAGE_BYTES + 1)
    framed = io.BytesIO(struct.pack("<I", len(payload)) + payload)
    with pytest.raises(ValueError, match="too large"):
        read_message(framed)
    assert framed.read() == b""


def test_serve_returns_error_and_continues() -> None:
    source = io.BytesIO(_frame({"id": 1}) + _frame({"id": 2}))
    target = io.BytesIO()
    serve(source, target, lambda message: (_ for _ in ()).throw(RuntimeError("no pair")) if message["id"] == 1 else {"id": 2, "ok": True})
    target.seek(0)
    assert read_message(target) == {"id": 1, "ok": False, "error": "request failed"}
    assert read_message(target) == {"id": 2, "ok": True}


def test_serve_bad_json_responds_and_continues() -> None:
    source = io.BytesIO(struct.pack("<I", 1) + b"{" + _frame({"id": 2}))
    target = io.BytesIO()
    serve(source, target, lambda message: {"id": message["id"], "ok": True, "result": "done"})
    target.seek(0)
    assert read_message(target) == {"ok": False, "error": "invalid message"}
    assert read_message(target) == {"id": 2, "ok": True, "result": "done"}


def test_serve_replaces_oversized_response_with_short_error() -> None:
    target = io.BytesIO()
    serve(io.BytesIO(_frame({"id": 9})), target, lambda _message: {"id": 9, "ok": True, "result": "x" * MAX_MESSAGE_BYTES})
    target.seek(0)
    assert read_message(target) == {"ok": False, "error": "response too large or invalid", "id": 9}


def test_input_over_limit_is_drained_then_loop_continues() -> None:
    oversized = b"x" * (MAX_INPUT_MESSAGE_BYTES + 1)
    source = io.BytesIO(
        struct.pack("<I", len(oversized)) + oversized + _frame({"id": 10})
    )
    target = io.BytesIO()
    serve(source, target, lambda message: {"id": message["id"], "ok": True})
    target.seek(0)
    assert read_message(target) == {"ok": False, "error": "invalid message"}
    assert read_message(target) == {"id": 10, "ok": True}

def test_server_executable_path_must_match_installed_python() -> None:
    from chimera.browser_host import _server_executable_matches

    expected = str(__import__("pathlib").Path("installed") / "python.exe")
    assert _server_executable_matches(expected, expected)
    assert not _server_executable_matches("attacker/python.exe", expected)


def test_registry_manifest_is_scoped_to_extension_id() -> None:
    from scripts.register_chrome_host import manifest

    value = manifest("a" * 32, "C:/Chimera/chimera-browser-host.cmd", "C:/Chimera/python.exe")
    assert value["allowed_origins"] == [f"chrome-extension://{'a' * 32}/"]
    assert value["type"] == "stdio"
    assert value["path"].endswith("chimera-browser-host.cmd")
    assert value["expected_server_executable"].endswith("python.exe")


def test_invalid_registry_ids_are_rejected_and_cmd_wrapper_is_described(capsys: pytest.CaptureFixture[str]) -> None:
    from scripts.register_chrome_host import main

    for extension_id in ("A" + "a" * 31, "q" * 32):
        with pytest.raises(SystemExit):
            import sys
            from unittest.mock import patch

            with patch.object(sys, "argv", ["register_chrome_host", extension_id]):
                main()
    valid_id = "a" * 32
    import sys
    from unittest.mock import patch

    with patch.object(sys, "argv", ["register_chrome_host", valid_id]):
        assert main() == 0
    printed = capsys.readouterr().out
    assert '"wrapper_path"' in printed
    assert '@echo off' in printed and ' -I -m chimera.browser_host' in printed


# The desktop app's copy of its service lives under the owner's %APPDATA%, one version folder per
# release. The host must keep accepting it after every update, and refuse any other image.
_SERVICE_ROOT = r"C:\Users\brcam\AppData\Roaming\app.chimera.desktop\service-bin"


def test_installed_service_is_accepted_for_any_version_folder() -> None:
    from chimera.browser_host import _installed_service_image_matches

    assert _installed_service_image_matches(rf"{_SERVICE_ROOT}\0.78.0\chimera-service.exe", _SERVICE_ROOT)
    assert _installed_service_image_matches(rf"{_SERVICE_ROOT}\0.78.1\chimera-service.exe", _SERVICE_ROOT)
    assert _installed_service_image_matches(rf"{_SERVICE_ROOT}\0.79.0-rc.1\chimera-service.exe", _SERVICE_ROOT)


def test_installed_service_match_ignores_windows_case() -> None:
    from chimera.browser_host import _installed_service_image_matches

    assert _installed_service_image_matches(rf"{_SERVICE_ROOT.upper()}\0.78.1\CHIMERA-SERVICE.EXE", _SERVICE_ROOT)


@pytest.mark.parametrize(
    "image",
    [
        r"C:\Users\other\AppData\Roaming\app.chimera.desktop\service-bin\0.78.1\chimera-service.exe",
        rf"{_SERVICE_ROOT}\..\..\..\evil\0.78.1\chimera-service.exe",
        rf"{_SERVICE_ROOT}\0.78.1\..\..\service-bin-evil\0.78.1\chimera-service.exe",
        rf"{_SERVICE_ROOT}-evil\0.78.1\chimera-service.exe",
        rf"{_SERVICE_ROOT}2\0.78.1\chimera-service.exe",
        rf"{_SERVICE_ROOT}\0.78.1\chimera-backend.exe",
        rf"{_SERVICE_ROOT}\evil\chimera-service.exe",
        rf"{_SERVICE_ROOT}\0.78.1\_internal\chimera-service.exe",
        rf"{_SERVICE_ROOT}\chimera-service.exe",
        rf"{_SERVICE_ROOT}\0.78.1",
        r"C:\Windows\System32\chimera-service.exe",
    ],
)
def test_other_images_are_refused_under_the_service_root(image: str) -> None:
    from chimera.browser_host import _installed_service_image_matches

    assert not _installed_service_image_matches(image, _SERVICE_ROOT)


def test_an_empty_service_root_refuses_everything() -> None:
    from chimera.browser_host import _installed_service_image_matches

    assert not _installed_service_image_matches(rf"{_SERVICE_ROOT}\0.78.1\chimera-service.exe", "")


class _PipePeer:
    _handle = 7


def _verdict_for_image(monkeypatch: pytest.MonkeyPatch, image: str) -> bool:
    from chimera import browser_host

    # Only the path normalisation is under test; realpath on a Windows string is meaningless on Linux.
    monkeypatch.setattr(browser_host.os.path, "realpath", lambda path, *_args, **_kwargs: path)
    monkeypatch.setattr(browser_host, "_server_executable_for_handle", lambda _handle, accept: accept(image))
    return browser_host._pipe_server_verified(_PipePeer())


def test_pipe_server_from_the_installed_root_survives_an_update(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CHIMERA_BROWSER_HOST_EXPECTED_EXE", raising=False)
    monkeypatch.setenv("CHIMERA_BROWSER_HOST_EXPECTED_SERVICE_ROOT", _SERVICE_ROOT)
    assert _verdict_for_image(monkeypatch, rf"{_SERVICE_ROOT}\0.78.0\chimera-service.exe")
    assert _verdict_for_image(monkeypatch, rf"{_SERVICE_ROOT}\0.78.1\chimera-service.exe")
    assert not _verdict_for_image(monkeypatch, rf"{_SERVICE_ROOT}\..\evil\chimera-service.exe")
    assert not _verdict_for_image(
        monkeypatch,
        r"C:\Users\other\AppData\Roaming\app.chimera.desktop\service-bin\0.78.1\chimera-service.exe",
    )


def test_pipe_server_exact_image_still_pins_a_dev_install(monkeypatch: pytest.MonkeyPatch) -> None:
    exact = r"C:\Users\brcam\Desktop\Agent AI\.venv\Scripts\python.exe"
    monkeypatch.setenv("CHIMERA_BROWSER_HOST_EXPECTED_EXE", exact)
    monkeypatch.delenv("CHIMERA_BROWSER_HOST_EXPECTED_SERVICE_ROOT", raising=False)
    assert _verdict_for_image(monkeypatch, exact)
    assert not _verdict_for_image(monkeypatch, rf"{_SERVICE_ROOT}\0.78.1\chimera-service.exe")


def test_an_empty_recorded_root_never_resolves_to_the_working_directory(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CHIMERA_BROWSER_HOST_EXPECTED_EXE", raising=False)
    monkeypatch.setenv("CHIMERA_BROWSER_HOST_EXPECTED_SERVICE_ROOT", "")
    assert not _verdict_for_image(monkeypatch, r"C:\anything\0.78.1\chimera-service.exe")


def test_registration_pins_the_service_root_for_an_installed_build() -> None:
    from pathlib import Path

    from scripts.register_chrome_host import installed_service_root

    image = Path(rf"{_SERVICE_ROOT}\0.78.1\chimera-service.exe")
    assert installed_service_root(image) == Path(_SERVICE_ROOT)


@pytest.mark.parametrize(
    "image",
    [
        r"C:\Users\brcam\AppData\Roaming\app.chimera.desktop\service-bin\0.78.1\chimera-backend.exe",
        r"C:\Users\brcam\AppData\Roaming\app.chimera.desktop\service-bin\nightly\chimera-service.exe",
        r"C:\Users\brcam\AppData\Roaming\other.app\service-bin\0.78.1\chimera-service.exe",
        r"C:\Users\brcam\Desktop\Agent AI\.venv\Scripts\python.exe",
    ],
)
def test_registration_keeps_the_exact_image_for_anything_else(image: str) -> None:
    from pathlib import Path

    from scripts.register_chrome_host import installed_service_root

    assert installed_service_root(Path(image)) is None


def test_registration_writes_the_service_root_into_wrapper_and_manifest(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    import sys
    from unittest.mock import patch

    from scripts.register_chrome_host import main

    with patch.object(sys, "argv", [
        "register_chrome_host", "a" * 32,
        "--python", str(tmp_path / "python.exe"),
        "--directory", str(tmp_path / "host"),
        "--server-executable", rf"{_SERVICE_ROOT}\0.78.1\chimera-service.exe",
    ]):
        assert main() == 0
    printed = capsys.readouterr().out
    assert "CHIMERA_BROWSER_HOST_EXPECTED_SERVICE_ROOT=" in printed
    assert "CHIMERA_BROWSER_HOST_EXPECTED_EXE=" not in printed
    assert '"expected_service_root"' in printed
    assert '"expected_server_executable"' not in printed


def _registered(capsys: pytest.CaptureFixture[str], tmp_path: Path, *extra: str) -> dict[str, object]:
    import sys
    from unittest.mock import patch

    from scripts.register_chrome_host import main

    argv = ["register_chrome_host", "a" * 32, "--python", str(tmp_path / "python.exe"),
            "--directory", str(tmp_path / "host"), *extra]
    with patch.object(sys, "argv", argv):
        assert main() == 0
    return json.loads(capsys.readouterr().out)


def test_desktop_service_path_gives_the_apps_data_home_in_wrapper_and_manifest(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    printed = _registered(
        capsys, tmp_path, "--server-executable", rf"{_SERVICE_ROOT}\0.78.1\chimera-service.exe"
    )
    home = printed["manifest"]["home"]
    # Endswith, not equality: on a non-Windows test host the registration resolves the path against
    # the working directory. The derivation itself is the Windows path <app.chimera.desktop>\data.
    assert home.endswith(r"app.chimera.desktop\data")
    assert f'set "CHIMERA_HOME={home}"' in printed["wrapper"]


def test_an_explicit_home_overrides_the_desktop_default(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    custom = tmp_path / "custom-home"
    printed = _registered(
        capsys,
        tmp_path,
        "--server-executable", rf"{_SERVICE_ROOT}\0.78.1\chimera-service.exe",
        "--home", str(custom),
    )
    assert printed["manifest"]["home"] == str(custom.resolve())
    assert f'set "CHIMERA_HOME={custom.resolve()}"' in printed["wrapper"]


def test_a_source_run_without_a_desktop_path_leaves_the_home_to_the_host(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    printed = _registered(capsys, tmp_path)
    assert "home" not in printed["manifest"]
    assert "CHIMERA_HOME" not in printed["wrapper"]
