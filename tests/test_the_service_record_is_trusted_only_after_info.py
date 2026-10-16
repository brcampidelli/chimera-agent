"""A service record is a claim; ``GET /api/info`` is the proof (S1, decision D5).

``~/.chimera/service-<channel>.json`` can be left behind by a crash, copied from another machine,
or edited. A client that believed it would hand the owner's work to whatever answers at that URL,
or replace a service it should have left alone. So every client probes before it trusts: the pid
must run, the server must accept the record's token, and its own pid, version, id and data folder
must be what the file says.

Each test breaks one of those and expects the probe to say so — `stale` for a dead pid,
`incompatible` for a version (or pid, or folder) the server contradicts, `unauthorized` for a token
it refuses. A probe that only checked "something answered" would pass all of them as alive.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

import pytest

from chimera.core.service_record import (
    ServiceRecord,
    load_or_create_token,
    new_record,
    probe,
    read_record,
    token_path,
    write_record,
)
from tests.service_harness import ServiceSandbox


@pytest.fixture(scope="module")
def live(tmp_path_factory: pytest.TempPathFactory) -> Iterator[ServiceRecord]:
    box = ServiceSandbox(tmp_path_factory.mktemp("live-service"))
    try:
        box.start()
        record = box.record()
        assert record is not None, box.log()
        yield record
    finally:
        box.close()


def _dead_pid() -> int:
    done = subprocess.run([sys.executable, "-c", "import os; print(os.getpid())"], capture_output=True, text=True)
    return int(done.stdout.strip())


def test_the_record_round_trips_and_a_malformed_one_is_no_record(tmp_path: Path) -> None:
    path = tmp_path / "service-dev.json"
    record = new_record(url="http://127.0.0.1:1", token="t", home=tmp_path)
    write_record(record, path)
    assert read_record(path) == record

    for broken in ("{", "[]", json.dumps({"id": "x"}), json.dumps({**record.to_dict(), "pid": "nope"})):
        path.write_text(broken, encoding="utf-8")
        assert read_record(path) is None, broken


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX file modes; Windows grants by ACL, not mode bits")
def test_the_record_and_the_token_are_readable_by_the_owner_only(tmp_path: Path) -> None:
    path = tmp_path / "svc" / "service-dev.json"
    write_record(new_record(url="http://127.0.0.1:1", token="t", home=tmp_path), path)
    load_or_create_token(tmp_path)

    for target in (path, token_path(tmp_path)):
        assert stat.S_IMODE(os.stat(target).st_mode) == 0o600, target


def test_a_record_whose_pid_is_gone_is_stale(tmp_path: Path) -> None:
    record = replace(new_record(url="http://127.0.0.1:9", token="t", home=tmp_path), pid=_dead_pid())
    assert probe(record).state == "stale"


def test_the_live_record_is_trusted(live: ServiceRecord) -> None:
    checked = probe(live)
    assert checked.trusted, checked.reason
    assert checked.info is not None and checked.info["mode"] == "service"


def test_an_edited_version_is_incompatible_not_alive(live: ServiceRecord) -> None:
    checked = probe(replace(live, version="99.0.0"))
    assert checked.state == "incompatible" and "version" in checked.reason


def test_a_record_naming_another_pid_or_folder_is_incompatible(live: ServiceRecord, tmp_path: Path) -> None:
    # Our own pid is alive, so the liveness check passes and the server's answer must catch it.
    assert probe(replace(live, pid=os.getpid())).state == "incompatible"
    assert probe(replace(live, home=str(tmp_path))).state == "incompatible"
    assert probe(replace(live, id="0" * 32)).state == "incompatible"


def test_a_token_the_server_refuses_is_unauthorized(live: ServiceRecord) -> None:
    assert probe(replace(live, token="guessed")).state == "unauthorized"


def test_status_reports_a_stale_record_and_start_refuses_a_tampered_one(tmp_path: Path, live: ServiceRecord) -> None:
    box = ServiceSandbox(tmp_path)
    try:
        write_record(replace(live, pid=_dead_pid(), home=str(box.home.resolve())), box.record_path)
        shown = box.cli("status", "--json")
        assert shown.returncode == 1 and json.loads(shown.stdout)["state"] == "stale"

        # A live server that contradicts the file: start must not believe it, and must not spawn.
        write_record(replace(live, version="99.0.0"), box.record_path)
        refused = box.cli("start", "--json", "--timeout", "5")
        assert refused.returncode == 1 and "not trusted" in refused.stdout, refused.stdout
        assert not (box.home / "logs" / "service.log").exists(), "start spawned a server anyway"
    finally:
        # The tampered record names the module's live service: drop it so cleanup cannot kill it.
        box.record_path.unlink(missing_ok=True)
        box.pids.discard(live.pid)
        box.close()
