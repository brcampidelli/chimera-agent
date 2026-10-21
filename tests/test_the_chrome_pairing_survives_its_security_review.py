"""The E5 security review found pairing that could not pair and a key one `echo` away.

Each test here pins one finding, so the fix cannot quietly come undone.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from chimera import config_vault
from chimera.browser_link import BrowserLink

_EXTENSION = Path(__file__).parents[1] / "extensions" / "chrome"


def _pattern_literals(source: str) -> list[str]:
    return re.findall(r"/\^.{1,12}\$/", source)


def test_the_six_digit_check_is_a_digit_class_in_both_scripts() -> None:
    # `/^\\d{6}$/` in JS is a backslash and a `d`: it never matched a real code, so the popup
    # refused every one and the service worker would have too.
    for name in ("popup.js", "service_worker.js"):
        literals = _pattern_literals((_EXTENSION / name).read_text(encoding="utf-8"))
        assert literals, name
        assert all(literal == "/^\\d{6}$/" for literal in literals), (name, literals)


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_popup_pattern_accepts_a_code_when_run() -> None:
    literal = _pattern_literals((_EXTENSION / "popup.js").read_text(encoding="utf-8"))[0]
    script = f"process.stdout.write(String({literal}.test('123456') && !{literal}.test('12345a')))"
    assert subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True).stdout == "true"


def test_pairing_is_asked_by_the_popup_never_by_the_host() -> None:
    source = (_EXTENSION / "service_worker.js").read_text(encoding="utf-8")
    runtime = source[source.index("chrome.runtime.onMessage.addListener"):]
    assert "request.op === 'pair'" in runtime and "pairUsingCode(request.code)" in runtime
    native = source[source.index("function connectNative"):source.index("chrome.runtime.onMessage.addListener")]
    assert "op === 'pair'" not in native


def test_the_pairing_key_never_reaches_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    assert "CHIMERA_BROWSER_PAIRING" in config_vault.STORABLE
    assert "CHIMERA_BROWSER_PAIRING" not in config_vault.startup_names(None)
    monkeypatch.delenv("CHIMERA_BROWSER_PAIRING", raising=False)

    class Vault:
        @staticmethod
        def get_password(_service: str, _name: str) -> str:
            return "ab" * 32

    monkeypatch.setattr(config_vault, "_keyring", lambda: Vault())
    # Only the pairing key is asked for, so no other name can land in the environment either.
    filled = config_vault.load_into_environment(names=("CHIMERA_BROWSER_PAIRING",), env_file=None)
    assert "CHIMERA_BROWSER_PAIRING" not in filled
    assert "CHIMERA_BROWSER_PAIRING" not in os.environ


def test_the_pairing_key_is_masked_if_it_ever_is_in_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    from chimera.core import redact

    secret = "cd" * 32
    monkeypatch.setenv("CHIMERA_BROWSER_PAIRING", secret)
    assert secret in redact.known_secrets()
    assert secret not in redact.redact(f"key={secret}")


def test_an_unpaired_link_opens_nothing_until_a_code_is_asked(tmp_path: Path) -> None:
    link = BrowserLink(tmp_path)
    link.start()
    try:
        assert link._thread is None and not link.listening_for_pairing()
        code, _ = link.issue_pair_code()
        assert len(code) == 6 and link.listening_for_pairing()
    finally:
        link.shutdown()


def test_no_code_is_issued_when_the_endpoint_cannot_open(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    link = BrowserLink(tmp_path)
    monkeypatch.setattr(link, "start", lambda **_kwargs: None)
    with pytest.raises(RuntimeError, match="not listening"):
        link.issue_pair_code()


def test_after_unpair_the_next_code_reopens_the_endpoint(tmp_path: Path) -> None:
    from chimera.browser_link import load_or_create_pairing_key

    assert load_or_create_pairing_key(tmp_path, create=True)
    link = BrowserLink(tmp_path)
    link.start()
    try:
        assert link._thread is not None and not link.listening_for_pairing()
        link.unpair()
        assert link._thread is None
        code, _ = link.issue_pair_code()
        assert len(code) == 6 and link.listening_for_pairing()
    finally:
        link.shutdown()


def test_a_silent_peer_does_not_stall_the_handshake() -> None:
    from chimera import browser_link

    class Silent:
        closed = False

        def send_bytes(self, _buf: bytes) -> None:
            return None

        def recv_bytes(self, maxlength: int | None = None) -> bytes:
            while not self.closed:
                time.sleep(0.01)
            raise EOFError

        def poll(self, timeout: float | None = 0.0) -> bool:
            return False

        def close(self) -> None:
            self.closed = True

    peer = Silent()
    started = time.monotonic()
    assert browser_link._handshake_within(peer, b"k" * 32, 0.2) is False
    assert time.monotonic() - started < 2
    assert peer.closed


def test_a_pipe_server_is_unverified_without_a_recorded_image(monkeypatch: pytest.MonkeyPatch) -> None:
    from chimera import browser_host

    monkeypatch.delenv("CHIMERA_BROWSER_HOST_EXPECTED_EXE", raising=False)
    monkeypatch.setattr(browser_host, "_registered_server_executable", lambda: None)
    assert browser_host._pipe_server_verified(object()) is False


def test_registration_records_the_base_interpreter_a_venv_launches(tmp_path: Path) -> None:
    from scripts.register_chrome_host import _base_interpreter

    base = tmp_path / "base"
    base.mkdir()
    (base / "python.exe").write_bytes(b"")
    venv = tmp_path / "venv"
    (venv / "Scripts").mkdir(parents=True)
    (venv / "Scripts" / "python.exe").write_bytes(b"")
    (venv / "pyvenv.cfg").write_text(f"home = {base}\nversion = 3.12\n", encoding="utf-8")
    assert _base_interpreter((venv / "Scripts" / "python.exe").resolve()) == (base / "python.exe").resolve()
    plain = tmp_path / "plain" / "python.exe"
    plain.parent.mkdir()
    plain.write_bytes(b"")
    assert _base_interpreter(plain.resolve()) == plain.resolve()


@pytest.mark.skipif(os.name != "nt", reason="the challenge runs on the accept thread only for the Windows pipe")
def test_a_peer_hanging_up_mid_handshake_does_not_end_the_link(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from chimera import browser_link
    from chimera.browser_link import load_or_create_pairing_key

    assert load_or_create_pairing_key(tmp_path, create=True)
    calls: list[int] = []

    def hang_up(_connection: object, _key: bytes, _timeout: float) -> bool:
        calls.append(1)
        raise EOFError

    class Peer:
        def close(self) -> None:
            return None

    class Listener:
        def accept(self) -> Peer:
            if len(calls) >= 3:
                link._stopping.set()
            return Peer()

        def close(self) -> None:
            return None

    monkeypatch.setattr(browser_link, "_handshake_within", hang_up)
    link = BrowserLink(tmp_path)
    link._listener = Listener()  # type: ignore[assignment]  # a stand-in with the two methods used
    link._thread = __import__("threading").current_thread()
    link._accept()
    # Three peers hung up in a row and the loop kept accepting instead of dying on the first.
    assert len(calls) >= 3


def test_after_shutdown_nothing_reopens_the_pipe(tmp_path: Path) -> None:
    link = BrowserLink(tmp_path)
    link.start(pairing=True)
    link.shutdown()
    link.unpair()
    with pytest.raises(RuntimeError, match="not listening"):
        link.issue_pair_code()
    assert link._thread is None
