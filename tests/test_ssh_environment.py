"""Where a Code conversation's commands run, part A of SSH: a host from the owner's ``~/.ssh/config``.

What these pin, in the order a mistake would cost most:

* **Only a named alias, only an allowed one.** An environment holds a ``Host`` alias and an absolute
  remote folder, never a user, an address, a password or a key. The alias must be in the owner's
  ``~/.ssh/config`` (no patterns) AND on the owner's allowed list, which only the app's own screen
  edits. Both are checked again at every turn.
* **The argv is a list with fixed security options.** No shell on this side, ``BatchMode`` and
  ``StrictHostKeyChecking=yes``, ``--`` before the alias, forwarding off, the folder quoted for the
  remote shell, and the command left exactly as the agent wrote it.
* **A command is never run twice.** Only a connection that provably failed before the remote shell
  started is retried; a host key that is not trusted is a refusal that says what to do.
* **SSH is the host.** Not isolated, so the host-exec gate, Manual and the "only reads" classifier
  treat it as the machine they guard; an install that isolates its commands refuses it.
* **The wrong machine is never edited.** The local file tools refuse in an SSH conversation, and a
  turn that has read content from outside asks the owner before a command on the remote host.

Nothing here needs ssh: the subprocess is a stub, and the config is a file in a temporary folder.
"""

from __future__ import annotations

import json
import shlex
import subprocess
from pathlib import Path
from typing import Any

import pytest

from chimera.config import get_settings
from chimera.core import environments as envs
from chimera.sandbox import ssh as ssh_module
from chimera.sandbox.base import SandboxResult
from chimera.sandbox.ssh import (
    SshPathError,
    SshSandbox,
    clean_remote_path,
    config_aliases,
    remote_command,
    ssh_argv,
    valid_alias,
)
from tests.test_code_environments import _Rig

CONFIG = """\
# the owner's file
Host *
    ServerAliveInterval 30

Host web1 db.internal
    HostName 10.0.0.1
    User deploy

Host *.corp web? !blocked
    User nobody

host=box
    HostName box.example.com

Match host nothing
    User x
"""


@pytest.fixture
def ssh_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A fake ``~/.ssh`` with a config, ssh 'installed', and nothing real read."""
    folder = tmp_path / "dotssh"
    folder.mkdir()
    (folder / "config").write_text(CONFIG, encoding="utf-8")
    monkeypatch.setattr(ssh_module, "ssh_dir", lambda: folder)
    monkeypatch.setattr(ssh_module, "ssh_supported", lambda: True)
    return folder


EXPECTED_OPTIONS = [
    "-o", "BatchMode=yes",
    "-o", "StrictHostKeyChecking=yes",
    "-o", "ServerAliveInterval=15",
    "-o", "ServerAliveCountMax=3",
    "-o", "ConnectTimeout=10",
    "-o", "ForwardAgent=no",
    "-o", "ClearAllForwardings=yes",
    "-o", "ForwardX11=no",
    "-o", "PermitLocalCommand=no",
    "-o", "RemoteCommand=none",
    "-o", "RequestTTY=no",
]


# -- ~/.ssh/config ----------------------------------------------------------------------------


def test_only_concrete_aliases_are_read_never_patterns(ssh_home: Path) -> None:
    assert config_aliases() == ["web1", "db.internal", "box"]


def test_include_is_followed_inside_dot_ssh_and_not_outside_it(ssh_home: Path, tmp_path: Path) -> None:
    (ssh_home / "conf.d").mkdir()
    (ssh_home / "conf.d" / "extra").write_text("Host inside1\n", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.write_text("Host outside1\n", encoding="utf-8")
    (ssh_home / "config").write_text(
        f"Include conf.d/*\nInclude ../outside\nInclude {outside}\nInclude config\nHost top\n",
        encoding="utf-8",
    )
    assert config_aliases() == ["inside1", "top"]


def test_a_quoted_host_pattern_is_one_word(ssh_home: Path) -> None:
    (ssh_home / "config").write_text(
        'Host "web 1" "star*" "quoted1" plain\nHost "unterminated\nHost after\n', encoding="utf-8"
    )
    assert config_aliases() == ["quoted1", "plain", "after"]


def test_a_missing_or_unreadable_config_offers_nothing(ssh_home: Path) -> None:
    (ssh_home / "config").unlink()
    assert config_aliases() == []
    assert config_aliases(ssh_home / "no-such-file") == []


@pytest.mark.parametrize(
    "name", ["-oProxyCommand=x", "a b", "", "*", "web1;rm", "user@web1", "web1\n", "a" * 65, "$(x)", "-web"]
)
def test_a_name_that_is_not_an_alias_is_refused(name: str) -> None:
    assert valid_alias(name) is False


# -- the remote folder ------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    ["srv/app", "~/app", "/srv/../etc", "/srv/app/..", "..", "/srv/a\nb", "/srv/a\x00b", "/srv\\app", "", "  ",
     "/" + "a" * 1100, "C:\\x"],
)
def test_a_remote_folder_that_is_not_an_absolute_posix_path_is_refused(path: str) -> None:
    with pytest.raises(SshPathError):
        clean_remote_path(path)


def test_a_remote_folder_is_cleaned_and_keeps_its_spaces() -> None:
    assert clean_remote_path(" /srv//my app/ ") == "/srv/my app"
    assert clean_remote_path("/") == "/"
    assert clean_remote_path("/a/b...c/..d") == "/a/b...c/..d"  # dots inside a name are not `..`


# -- the argv ---------------------------------------------------------------------------------


def test_the_argv_is_a_list_with_the_fixed_options_the_alias_after_dashes_and_one_command() -> None:
    argv = ssh_argv("web1", remote_command("/srv/app", "ls -la && echo done"))
    assert argv == [
        "ssh", *EXPECTED_OPTIONS, "--", "web1", "cd /srv/app || exit 1; ls -la && echo done",
    ]


def test_a_folder_with_a_space_or_a_quote_is_quoted_for_the_remote_shell() -> None:
    folder = "/srv/my app/it's; rm -rf $HOME"
    remote = remote_command(folder, "ls")
    head, tail = remote.split(" || exit 1; ")
    assert tail == "ls"
    assert shlex.split(head) == ["cd", folder]  # the remote shell reads exactly the one folder
    assert head == f"cd {shlex.quote(folder)}"


def test_a_failed_cd_ends_the_command_instead_of_running_the_rest_in_the_home_folder() -> None:
    assert remote_command("/srv/app", "a; b") == "cd /srv/app || exit 1; a; b"


def test_the_agents_command_is_not_rewritten() -> None:
    command = "echo 'a b' | grep \"x\" > /tmp/out; true $(date)"
    assert remote_command("/srv", command).endswith(f"; {command}")


def test_a_bad_alias_never_reaches_the_argv() -> None:
    with pytest.raises(SshPathError):
        ssh_argv("-oProxyCommand=evil", "ls")


class _Proc:
    def __init__(self, code: int, out: bytes, err: bytes, hang: bool = False, log: bytes = b"") -> None:
        self.returncode = code
        self.pid = 4242
        self._out, self._err, self._hang = out, err, hang
        #: What the ssh CLIENT wrote to its own log (-E): never mixed with the command's stderr.
        self.log = log

    def communicate(self, timeout: float | None = None) -> tuple[bytes, bytes]:
        if self._hang:
            self._hang = False
            raise subprocess.TimeoutExpired("ssh", timeout or 0)
        return self._out, self._err

    def poll(self) -> int:
        return self.returncode


class _Ssh:
    """A stand-in for ``subprocess.Popen`` that answers from a script and records every call."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, script: list[_Proc]) -> None:
        self.calls: list[dict[str, Any]] = []
        self.script = script
        self.waits: list[float] = []
        self.killed: list[Any] = []
        monkeypatch.setattr(subprocess, "Popen", self._popen)
        monkeypatch.setattr(ssh_module, "_sleep", self.waits.append)
        monkeypatch.setattr("chimera.sandbox.local.kill_tree", self.killed.append)

    def _popen(self, target: Any, **kwargs: Any) -> _Proc:
        self.calls.append({"target": target, **kwargs})
        proc = self.script.pop(0) if len(self.script) > 1 else self.script[0]
        Path(target[target.index("-E") + 1]).write_bytes(proc.log)
        return proc


CONNECT_FAIL = b"ssh: connect to host web1 port 22: Connection timed out\r\n"


def _bare(target: list[str]) -> list[str]:
    """The argv without the ``-E <log file>`` pair, whose path is a fresh temporary file each time."""
    at = target.index("-E")
    return [*target[:at], *target[at + 2:]]


def test_the_sandbox_runs_ssh_with_an_argv_list_never_a_shell_and_a_scrubbed_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MY_SERVICE_API_KEY", "super-secret-value")
    fake = _Ssh(monkeypatch, [_Proc(0, b"hello\n", b"")])
    result = SshSandbox("web1", "/srv/my app").run("ls -la", timeout=5)
    assert result.exit_code == 0 and result.stdout == "hello\n"
    (call,) = fake.calls
    assert _bare(call["target"]) == [
        "ssh", *EXPECTED_OPTIONS, "--", "web1", "cd '/srv/my app' || exit 1; ls -la",
    ]
    assert call["shell"] is False
    assert "super-secret-value" not in json.dumps(call["env"])


def test_a_working_directory_below_the_workspace_becomes_a_folder_below_the_remote_one(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = _Ssh(monkeypatch, [_Proc(0, b"", b"")])
    box = SshSandbox("web1", "/srv/app", tmp_path)
    box.run("ls", cwd=tmp_path / "src" / "lib")
    box.run("ls", cwd=tmp_path)
    box.run("ls")
    remotes = [c["target"][-1] for c in fake.calls]
    assert remotes == [
        "cd /srv/app/src/lib || exit 1; ls", "cd /srv/app || exit 1; ls", "cd /srv/app || exit 1; ls",
    ]


def test_a_working_directory_outside_the_workspace_is_not_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = _Ssh(monkeypatch, [_Proc(0, b"", b"")])
    box = SshSandbox("web1", "/srv/app", tmp_path / "ws")
    for outside in (tmp_path, tmp_path / "other", tmp_path / "ws" / ".." / "x"):
        result = box.run("ls", cwd=outside)
        assert result.exit_code == 1 and "not run" in result.stderr
    assert fake.calls == []


# -- reconnecting, and what is never repeated -------------------------------------------------


def test_an_unknown_or_changed_host_key_is_a_clear_refusal_and_is_not_retried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for text in (b"Host key verification failed.\r\n", b"@@@ WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED! @@@\n"):
        fake = _Ssh(monkeypatch, [_Proc(255, b"", b"", log=text)])
        result = SshSandbox("web1", "/srv").run("ls")
        assert len(fake.calls) == 1 and fake.waits == []
        assert result.exit_code == 255
        assert "host key is not trusted" in result.stderr
        assert "ssh web1" in result.stderr and "never accepts a new host key" in result.stderr


def test_a_refused_credential_says_to_load_the_key_and_is_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _Ssh(monkeypatch, [_Proc(255, b"", b"", log=b"deploy@web1: Permission denied (publickey).\n")])
    result = SshSandbox("web1", "/srv").run("ls")
    assert len(fake.calls) == 1
    assert "ssh-agent" in result.stderr


def test_a_connection_that_never_started_is_retried_a_limited_number_of_times(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _Ssh(monkeypatch, [_Proc(255, b"", b"", log=CONNECT_FAIL)])
    result = SshSandbox("web1", "/srv").run("make deploy", timeout=60)
    assert len(fake.calls) == 1 + ssh_module.CONNECT_RETRIES
    assert fake.waits == [ssh_module.RETRY_WAIT_SECONDS] * ssh_module.CONNECT_RETRIES
    assert result.exit_code == 255


def test_a_retry_that_connects_runs_once_more_and_stops(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _Ssh(monkeypatch, [_Proc(255, b"", b"", log=CONNECT_FAIL), _Proc(0, b"deployed\n", b"")])
    result = SshSandbox("web1", "/srv").run("make deploy", timeout=60)
    assert len(fake.calls) == 2 and result.exit_code == 0 and result.stdout == "deployed\n"


FORGED = b"ssh: connect to host web1 port 22: Connection refused\n"


@pytest.mark.parametrize(
    ("out", "err", "log"),
    [
        (b"partial output\n", b"", CONNECT_FAIL),  # it printed something: the command ran
        (b"", FORGED, b""),  # the REMOTE command wrote ssh's own sentence to its stderr, then exit 255
        (b"", FORGED, FORGED),  # forged AND the client really failed after output on stderr
        (b"", b"", b"Timeout, server web1 not responding.\n"),  # the session died mid-run
        (b"", b"", b"Connection closed by 10.0.0.1 port 22\n"),  # could be after the command started
        (b"", b"", b""),  # exit 255 and nothing said by anyone: a command's own exit code
    ],
    ids=["output", "forged-stderr", "forged-and-stderr", "midrun", "closed", "silent"],
)
def test_a_255_that_may_have_run_the_command_is_never_run_again(
    out: bytes, err: bytes, log: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _Ssh(monkeypatch, [_Proc(255, out, err, log=log)])
    result = SshSandbox("web1", "/srv").run("rm -rf build && make")
    assert len(fake.calls) == 1 and fake.waits == []
    assert result.exit_code == 255


def test_a_command_that_prints_ssh_looking_lines_and_exits_255_runs_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """The review's case: `echo '<ssh sentence>' >&2; exit 255`, byte for byte what the client says."""
    fake = _Ssh(monkeypatch, [_Proc(255, b"", FORGED)])
    result = SshSandbox("web1", "/srv").run("echo 'ssh: connect to host x port 22: Connection refused' >&2; exit 255")
    assert len(fake.calls) == 1 and fake.waits == []
    assert "Connection refused" in result.stderr  # the command's own stderr still reaches the agent


def test_a_nested_ssh_that_fails_on_the_remote_side_is_not_a_failed_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    nested = b"ssh: connect to host db port 22: Connection timed out\n"
    fake = _Ssh(monkeypatch, [_Proc(255, b"", nested)])
    SshSandbox("web1", "/srv").run("make migrate && ssh db ./restart")
    assert len(fake.calls) == 1 and fake.waits == []


def test_the_clients_own_log_never_reaches_the_agent_raw(monkeypatch: pytest.MonkeyPatch) -> None:
    log = (
        b"debug1: Reading configuration data /home/me/.ssh/config\n"
        b"Identity file /home/me/.ssh/id_ed25519_prod type -1\n"
        b"Bad owner or permissions on /home/me/.ssh/config\n"
        b"ssh: Could not resolve hostname secret.internal.example: Name or service not known\n"
        b"kex failed to 10.20.30.40 port 2222 and 2001:db8::1\n"
    )
    _Ssh(monkeypatch, [_Proc(255, b"", b"remote said boom\n", log=log)])
    result = SshSandbox("web1", "/srv").run("ls")
    for leaked in ("secret.internal", "/home/me", "id_ed25519", "10.20.30.40", "2222", "2001:db8", "Identity file"):
        assert leaked not in result.stderr, leaked
    assert "web1" in result.stderr and "remote said boom" in result.stderr


def test_a_hop_of_a_proxy_jump_writes_to_stderr_and_is_cleaned_the_same_way(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    err = (
        b"ssh: connect to host 10.0.0.5 port 2222: Connection refused\n"
        b"Identity file /home/x/.ssh/id_x not accessible: No such file or directory.\n"
        b"kex_exchange_identification: Connection closed by remote host\n"
    )
    fake = _Ssh(monkeypatch, [_Proc(255, b"", err)])  # exit 255, the client log is empty
    result = SshSandbox("web1", "/srv").run("ls")
    assert len(fake.calls) == 1  # still not a provable pre-connection failure: never re-run
    for leaked in ("10.0.0.5", "2222", "/home/x", "id_x", "Identity file"):
        assert leaked not in result.stderr, leaked
    assert "connect to web1" in result.stderr


def test_a_log_file_that_cannot_be_created_is_a_not_run_result_and_not_an_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _Ssh(monkeypatch, [_Proc(0, b"", b"")])

    def broken(**_: Any) -> Any:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr("chimera.sandbox.ssh.tempfile.mkstemp", broken)
    result = SshSandbox("web1", "/srv").run("ls")
    assert result.exit_code == 1 and result.stderr.startswith("error: not run:")
    assert fake.calls == []


def test_a_failed_connection_after_the_retries_names_the_alias_and_not_the_address(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    log = b"ssh: connect to host 203.0.113.9 port 2222: Connection refused\n"
    _Ssh(monkeypatch, [_Proc(255, b"", b"", log=log)])
    result = SshSandbox("web1", "/srv").run("ls", timeout=60)
    assert "203.0.113.9" not in result.stderr and "2222" not in result.stderr
    assert "connect to web1" in result.stderr


def test_the_client_log_file_is_removed_after_each_command(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _Ssh(monkeypatch, [_Proc(0, b"", b"")])
    SshSandbox("web1", "/srv").run("true")
    target = fake.calls[0]["target"]
    assert not Path(target[target.index("-E") + 1]).exists()


def test_the_host_is_checked_again_at_each_command(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _Ssh(monkeypatch, [_Proc(0, b"ok", b"")])
    box = SshSandbox("web1", "/srv")
    state = {"allowed": True}

    def recheck() -> None:
        if not state["allowed"]:
            raise ValueError("'web1' is not on your list of allowed SSH hosts")

    box.bind_recheck(recheck)
    assert box.run("a").exit_code == 0
    state["allowed"] = False  # removed from the list in the middle of the turn
    refused = box.run("b")
    assert refused.exit_code == 1 and "not run" in refused.stderr and "allowed" in refused.stderr
    assert len(fake.calls) == 1


def test_other_failures_are_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _Ssh(monkeypatch, [_Proc(1, b"", b"ssh: connect to host web1 port 22: Connection refused\n")])
    SshSandbox("web1", "/srv").run("false")  # exit 1 is the command's, whatever it printed
    assert len(fake.calls) == 1


def test_a_timeout_kills_the_local_process_and_is_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _Ssh(monkeypatch, [_Proc(0, b"", b"", hang=True)])
    result = SshSandbox("web1", "/srv").run("sleep 999", timeout=2)
    assert result.timed_out and result.exit_code == 124
    assert len(fake.killed) == 1 and len(fake.calls) == 1


# -- the saved environment --------------------------------------------------------------------


def _registry(tmp_path: Path) -> envs.EnvironmentRegistry:
    return envs.registry_for(tmp_path / "home")


def test_an_alias_that_is_not_in_the_config_or_not_allowed_cannot_be_saved(
    tmp_path: Path, ssh_home: Path
) -> None:
    registry = _registry(tmp_path)
    good = {"host": "web1", "remote_path": "/srv/app/"}
    with pytest.raises(ValueError, match="not on your list of allowed"):
        registry.add("prod", "ssh", good)  # in the config, not allowed yet
    with pytest.raises(ValueError, match="not a Host alias in your"):
        registry.allow_ssh_host("ghost")
    for pattern in ("*", "*.corp", "web?", "blocked", "!blocked"):
        with pytest.raises(ValueError):
            registry.allow_ssh_host(pattern)
    assert registry.ssh_hosts() == []
    registry.allow_ssh_host("web1")
    made = registry.add("prod", "ssh", good)
    assert made.params == {"host": "web1", "remote_path": "/srv/app"}  # cleaned
    with pytest.raises(ValueError, match="not on your list of allowed"):
        registry.add("other", "ssh", {"host": "box", "remote_path": "/x"})


@pytest.mark.parametrize(
    "params",
    [
        {}, {"host": "web1"}, {"remote_path": "/srv"},
        {"host": "web1", "remote_path": "relative"},
        {"host": "web1", "remote_path": "/srv/../etc"},
        {"host": "-oProxyCommand=x", "remote_path": "/srv"},
        {"host": "user@web1", "remote_path": "/srv"},
        {"host": "web1", "remote_path": "/srv", "user": "root"},
        {"host": "web1", "remote_path": "/srv", "password": "hunter2"},
        {"host": "web1", "remote_path": "/srv", "identity_file": "/home/me/.ssh/id_ed25519"},
        {"host": "web1", "remote_path": "/srv", "port": "2222"},
    ],
)
def test_an_ssh_environment_takes_an_alias_and_a_folder_and_nothing_else(
    params: dict[str, str], tmp_path: Path, ssh_home: Path
) -> None:
    registry = _registry(tmp_path)
    registry.allow_ssh_host("web1")
    with pytest.raises(ValueError):
        registry.add("x", "ssh", params)
    assert registry.entries() == []


def test_the_files_the_owner_keeps_hold_an_alias_and_a_folder_and_never_a_secret(
    tmp_path: Path, ssh_home: Path
) -> None:
    registry = _registry(tmp_path)
    registry.allow_ssh_host("web1")
    registry.add("prod", "ssh", {"host": "web1", "remote_path": "/srv/app"})
    text = (tmp_path / "home" / "environments.json").read_text("utf-8")
    hosts = (tmp_path / "home" / "ssh_hosts.json").read_text("utf-8")
    assert json.loads(text)["environments"][0]["params"] == {"host": "web1", "remote_path": "/srv/app"}
    assert json.loads(hosts) == {"hosts": ["web1"]}
    for secret in ("HostName", "10.0.0.1", "deploy", "password", "IdentityFile"):
        assert secret not in text and secret not in hosts


def test_ssh_cannot_be_saved_where_there_is_no_ssh(
    tmp_path: Path, ssh_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry = _registry(tmp_path)
    registry.allow_ssh_host("web1")
    monkeypatch.setattr(ssh_module, "ssh_supported", lambda: False)
    assert envs.kind_available("ssh") is False
    with pytest.raises(ValueError, match="not available on this machine"):
        registry.add("prod", "ssh", {"host": "web1", "remote_path": "/srv"})


def test_a_hand_edited_row_or_host_list_that_the_form_would_refuse_is_not_read(
    tmp_path: Path, ssh_home: Path
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    (home / "environments.json").write_text(json.dumps({"environments": [
        {"id": "a", "name": "ok", "kind": "ssh", "params": {"host": "web1", "remote_path": "/srv"}},
        {"id": "b", "name": "bad", "kind": "ssh", "params": {"host": "web1", "remote_path": "/srv/../x"}},
        {"id": "c", "name": "worse", "kind": "ssh", "params": {"host": "web1", "remote_path": "/s", "user": "r"}},
    ]}), encoding="utf-8")
    (home / "ssh_hosts.json").write_text(
        json.dumps({"hosts": ["web1", "-bad", "a b", 7, "web1", "*"]}), encoding="utf-8"
    )
    registry = envs.registry_for(home)
    assert [e.id for e in registry.entries()] == ["a"]
    assert registry.ssh_hosts() == ["web1"]
    (home / "ssh_hosts.json").write_text("not json", encoding="utf-8")
    assert registry.ssh_hosts() == []
    assert (home / "ssh_hosts.json.damaged").exists()


def test_removing_a_host_from_the_list_or_the_config_stops_the_environments_that_use_it(
    tmp_path: Path, ssh_home: Path
) -> None:
    from chimera.config import Settings

    registry = _registry(tmp_path)
    registry.allow_ssh_host("web1")
    made = registry.add("prod", "ssh", {"host": "web1", "remote_path": "/srv"})
    settings = Settings(CHIMERA_HOME=str(tmp_path / "home"), CHIMERA_SANDBOX="local")
    box = envs.sandbox_for(made, tmp_path, settings)
    assert isinstance(box, SshSandbox) and box.host == "web1" and box.remote_path == "/srv"
    assert registry.usable(made) is True

    assert registry.forget_ssh_host("web1") is True and registry.forget_ssh_host("web1") is False
    assert registry.usable(made) is False
    with pytest.raises(envs.EnvironmentRefused) as refused:
        envs.sandbox_for(made, tmp_path, settings)
    assert refused.value.status == 409 and "allowed" in refused.value.detail

    registry.allow_ssh_host("web1")
    (ssh_home / "config").write_text("Host other\n", encoding="utf-8")  # the alias left the config
    with pytest.raises(envs.EnvironmentRefused) as gone:
        envs.sandbox_for(made, tmp_path, settings)
    assert gone.value.status == 409 and "~/.ssh/config" in gone.value.detail


def test_a_host_removed_in_the_middle_of_a_turn_stops_the_next_command(
    tmp_path: Path, ssh_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from chimera.config import Settings

    registry = _registry(tmp_path)
    registry.allow_ssh_host("web1")
    made = registry.add("prod", "ssh", {"host": "web1", "remote_path": "/srv"})
    settings = Settings(CHIMERA_HOME=str(tmp_path / "home"), CHIMERA_SANDBOX="local")
    fake = _Ssh(monkeypatch, [_Proc(0, b"ok", b"")])
    box = envs.sandbox_for(made, tmp_path, settings)
    assert box is not None and box.run("first").exit_code == 0
    registry.forget_ssh_host("web1")
    refused = box.run("second")
    assert refused.exit_code == 1 and "allowed" in refused.stderr
    assert len(fake.calls) == 1


def test_an_install_that_isolates_its_commands_refuses_the_ssh_environment(
    tmp_path: Path, ssh_home: Path
) -> None:
    from chimera.config import Settings

    registry = _registry(tmp_path)
    registry.allow_ssh_host("web1")
    made = registry.add("prod", "ssh", {"host": "web1", "remote_path": "/srv"})
    settings = Settings(CHIMERA_HOME=str(tmp_path / "home"), CHIMERA_SANDBOX="docker")
    with pytest.raises(envs.EnvironmentRefused) as refused:
        envs.sandbox_for(made, tmp_path, settings)
    assert refused.value.status == 409 and "isolated" in refused.value.detail


# -- ssh is the host --------------------------------------------------------------------------


def test_ssh_is_not_isolated_and_is_not_a_local_sandbox() -> None:
    from chimera.sandbox import LocalSandbox
    from chimera.sandbox.confirm import sandbox_is_isolated

    box = SshSandbox("web1", "/srv")
    assert box.is_isolated() is False and sandbox_is_isolated(box) is False
    assert not isinstance(box, LocalSandbox)


def test_ssh_counts_as_the_host_for_the_host_exec_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    from chimera.tools.shell import RunShellTool

    ran: list[str] = []

    def record(self: SshSandbox, command: str, **_: Any) -> SandboxResult:
        ran.append(command)
        return SandboxResult(exit_code=0, stdout="ok")

    monkeypatch.setattr(SshSandbox, "run", record)
    asked: list[str] = []

    def decline(command: str) -> bool:
        asked.append(command)
        return False

    tool = RunShellTool(Path.cwd(), SshSandbox("web1", "/srv"), confirm=decline)
    assert "declined" in tool.run(command="echo hi")
    assert asked == ["echo hi"] and ran == []
    allowed = RunShellTool(Path.cwd(), SshSandbox("web1", "/srv"), confirm=lambda _c: True)
    assert "ok" in allowed.run(command="echo hi") and ran == ["echo hi"]


def test_an_ssh_turn_offers_no_background_job() -> None:
    from chimera.core.jobs import jobs_for
    from chimera.tools.shell import RunShellTool

    tool = RunShellTool(
        Path.cwd(), SshSandbox("web1", "/srv"), confirm=lambda _c: True, jobs=jobs_for(Path.cwd() / ".j")
    )
    assert "not available in an SSH environment" in tool.run(command="sleep 1", background=True)


def test_the_shell_tool_says_where_it_runs_and_the_confirmation_names_the_host(tmp_path: Path) -> None:
    from chimera.tools import default_registry

    registry = default_registry(tmp_path, host_exec_confirm=None, sandbox=SshSandbox("web1", "/srv/app"))
    description = registry.get("run_shell").description
    assert "run on web1 over ssh, in /srv/app" in description


# -- the wrong machine is never edited --------------------------------------------------------


LOCAL_FILE_TOOLS = (
    "read_file", "write_file", "edit_file", "apply_patch", "list_dir", "grep", "glob", "execute_code",
)


def test_the_local_file_tools_refuse_in_an_ssh_conversation_and_touch_nothing(tmp_path: Path) -> None:
    from chimera.tools import default_registry

    (tmp_path / "keep.txt").write_text("local file", encoding="utf-8")
    registry = default_registry(tmp_path, host_exec_confirm=None, sandbox=SshSandbox("web1", "/srv/app", tmp_path))
    for name in LOCAL_FILE_TOOLS:
        tool = registry.get(name)
        args = {key: "keep.txt" for key in tool.parameters.get("required", [])}
        out = tool.run(**args)
        assert out == (
            "error: this conversation runs on web1; use run_shell to read or edit files there. "
            "This tool works on a folder of the machine running Chimera, not on the remote host, so it "
            "was not run."
        ), name
        assert "UNAVAILABLE" in tool.description, name
    assert sorted(p.name for p in tmp_path.iterdir()) == ["keep.txt"]
    assert (tmp_path / "keep.txt").read_text("utf-8") == "local file"
    # The shell is the way in, and it is not refused.
    assert "UNAVAILABLE" not in registry.get("run_shell").description


def test_execute_code_does_not_write_a_script_here_for_a_remote_host(tmp_path: Path) -> None:
    from chimera.tools.code import ExecuteCodeTool

    tool = ExecuteCodeTool(tmp_path, SshSandbox("web1", "/srv"), confirm=lambda _c: True)
    assert "runs on web1" in tool.run(code="print(1)")
    assert list(tmp_path.iterdir()) == []


def test_the_design_tool_registered_after_the_default_registry_is_blocked_too(tmp_path: Path) -> None:
    """`design export` writes into this machine's project folder, and is registered in `assemble_registry`."""
    from chimera.api.code_api import CodeSeams, assemble_registry
    from chimera.config import Settings
    from chimera.providers.gateway import LLMGateway
    from chimera.tools.design import DesignOffer
    from chimera.tools.ssh_files import RefusedLocalFileTool

    ws = tmp_path / "ws"
    ws.mkdir()
    settings = Settings(CHIMERA_HOME=str(tmp_path / "home"))
    box = SshSandbox("web1", "/srv", ws)
    offer = DesignOffer()
    registry, _ = assemble_registry(
        CodeSeams(), ws, settings, LLMGateway(), steps=4, surface="api:turn", sandbox=box, design=offer
    )
    assert offer.offered is True
    tool = registry.get("design")
    while tool is not None and not isinstance(tool, RefusedLocalFileTool):
        tool = getattr(tool, "_inner", None) or getattr(tool, "inner", None)
    assert tool is not None, "the design tool is not refused in an SSH conversation"
    assert "runs on web1" in tool.run(action="export")
    assert not (ws / "design-exports").exists()
    # A local conversation keeps it.
    local, _ = assemble_registry(
        CodeSeams(), ws, settings, LLMGateway(), steps=4, surface="api:turn", design=DesignOffer()
    )
    inner = local.get("design")
    while inner is not None and not isinstance(inner, RefusedLocalFileTool) and (
        getattr(inner, "_inner", None) or getattr(inner, "inner", None)
    ):
        inner = getattr(inner, "_inner", None) or getattr(inner, "inner", None)
    assert not isinstance(inner, RefusedLocalFileTool)


def test_a_local_conversation_keeps_every_file_tool(tmp_path: Path) -> None:
    from chimera.tools import default_registry
    from chimera.tools.ssh_files import RefusedLocalFileTool

    registry = default_registry(tmp_path, host_exec_confirm=None)
    for name in LOCAL_FILE_TOOLS:
        assert not isinstance(registry.get(name), RefusedLocalFileTool), name
    (tmp_path / "a.txt").write_text("hello", encoding="utf-8")
    assert "hello" in registry.get("read_file").run(path="a.txt")


# -- a turn that read content from outside asks first -----------------------------------------


def _assembled(tmp_path: Path, sink: Any, box: SshSandbox, **extra: Any) -> Any:
    from chimera.api.code_api import CodeSeams, assemble_registry
    from chimera.config import Settings
    from chimera.providers.gateway import LLMGateway

    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    settings = Settings(CHIMERA_HOME=str(tmp_path / "home"), CHIMERA_APPROVAL_MODE="ask", CHIMERA_APPROVAL_WAIT="5")
    _, ledger = assemble_registry(
        CodeSeams(), ws, settings, LLMGateway(), steps=4, surface="api:turn", approval_sink=sink, sandbox=box,
        **extra,
    )
    return settings, ledger


def test_a_clean_turn_runs_without_the_taint_question(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from chimera.governance.approval import ApprovalAnnouncer

    fake = _Ssh(monkeypatch, [_Proc(0, b"fine\n", b"")])
    sink = ApprovalAnnouncer()
    asked: list[Any] = []
    sink.emit = asked.append
    box = SshSandbox("web1", "/srv")
    _assembled(tmp_path, sink, box)
    assert box.run("uptime").stdout == "fine\n"
    assert asked == [] and len(fake.calls) == 1


def test_a_tainted_turn_puts_the_command_on_the_owners_card_and_a_no_stops_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from chimera.core.task_cards import seed_taint
    from chimera.governance import pending
    from chimera.governance.approval import ApprovalAnnouncer

    fake = _Ssh(monkeypatch, [_Proc(0, b"ran\n", b"")])
    sink = ApprovalAnnouncer()
    asked: list[Any] = []
    answers = iter([True, False])

    def on_screen(question: Any) -> None:
        asked.append(question)
        assert pending.answer(settings.home, question.id, next(answers))

    sink.emit = on_screen
    box = SshSandbox("web1", "/srv")
    settings, ledger = _assembled(tmp_path, sink, box)
    seed_taint(ledger)

    allowed = box.run("cat /etc/hosts")
    assert allowed.exit_code == 0 and len(fake.calls) == 1
    refused = box.run("uname -a")
    assert refused.exit_code == 1 and "needs the owner's approval" in refused.stderr
    assert len(fake.calls) == 1  # the declined command never started
    assert len(asked) == 2
    assert "run_shell: [ssh web1] cat /etc/hosts" in asked[0].action
    assert "web1" in asked[0].reason


def test_with_ignore_permissions_on_a_tainted_ssh_command_shows_no_card_exactly_as_the_local_host_does(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The owner's decision of 26/10: "Ignore permissions" releases what asks, tainted turns included.

    SSH is the host, so it follows the same rule and is not stricter: no card is shown, the release is
    announced as a notice (not a question), and the command runs.
    """
    from chimera.core.task_cards import seed_taint
    from chimera.governance.approval import ApprovalAnnouncer

    fake = _Ssh(monkeypatch, [_Proc(0, b"ran\n", b"")])
    sink = ApprovalAnnouncer()
    seen: list[Any] = []
    sink.emit = seen.append
    box = SshSandbox("web1", "/srv")
    _, ledger = _assembled(tmp_path, sink, box, bypass_permissions=True)
    seed_taint(ledger)
    assert box.run("uptime").exit_code == 0 and len(fake.calls) == 1
    cards = [e for e in seen if not isinstance(e, dict)]
    assert cards == [], "a card was shown although Ignore permissions is on"
    assert any(isinstance(e, dict) and "aprovado sozinho" in str(e.get("text")) for e in seen)


def test_with_ignore_permissions_off_the_same_tainted_command_does_show_the_card(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from chimera.core.task_cards import seed_taint
    from chimera.governance import pending
    from chimera.governance.approval import ApprovalAnnouncer

    fake = _Ssh(monkeypatch, [_Proc(0, b"ran\n", b"")])
    sink = ApprovalAnnouncer()
    cards: list[Any] = []

    def on_screen(question: Any) -> None:
        cards.append(question)
        assert pending.answer(settings.home, question.id, True)

    sink.emit = on_screen
    box = SshSandbox("web1", "/srv")
    settings, ledger = _assembled(tmp_path, sink, box, bypass_permissions=False)
    seed_taint(ledger)
    assert box.run("uptime").exit_code == 0 and len(cards) == 1 and len(fake.calls) == 1


@pytest.mark.parametrize("where", ["ssh", "local"])
def test_a_refusal_on_the_card_silences_the_turns_remaining_commands_and_the_next_turn_starts_clean(
    where: str,
) -> None:
    """`HostExecTurnLimiter`: one "no" on the command card stops every later `run_shell` of THAT turn.

    The same for SSH and for the local host (both are not isolated). The limiter is built with the
    turn's registry, so the next turn asks again. The taint question above is the ledger's approver,
    not this latch: it asks per command.
    """
    from chimera.sandbox import LocalSandbox
    from chimera.tools.shell import HOST_EXEC_ALREADY_DECLINED, HostExecTurnLimiter, RunShellTool

    box: Any = SshSandbox("web1", "/srv") if where == "ssh" else LocalSandbox()
    asked: list[str] = []

    def turn() -> RunShellTool:
        def no(command: str) -> bool:
            asked.append(command)
            return False

        return RunShellTool(Path.cwd(), box, confirm=HostExecTurnLimiter(no, lambda: True))

    first = turn()
    assert "declined" in first.run(command="echo one")
    assert first.run(command="echo two") == HOST_EXEC_ALREADY_DECLINED
    assert first.run(command="echo three") == HOST_EXEC_ALREADY_DECLINED
    assert asked == ["echo one"]
    second = turn()  # the next turn
    assert "declined" in second.run(command="echo four")
    assert asked == ["echo one", "echo four"]


def test_a_tainted_turn_with_no_screen_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from chimera.core.task_cards import seed_taint

    fake = _Ssh(monkeypatch, [_Proc(0, b"ran\n", b"")])
    box = SshSandbox("web1", "/srv")
    _, ledger = _assembled(tmp_path, None, box)
    seed_taint(ledger)
    result = box.run("uptime")
    assert result.exit_code == 1 and "not run" in result.stderr
    assert fake.calls == []


def test_binding_without_a_question_refuses_a_tainted_command_and_never_asks_a_clean_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _Ssh(monkeypatch, [_Proc(0, b"ok", b"")])
    box = SshSandbox("web1", "/srv")
    state = {"tainted": False}
    asked: list[str] = []
    box.bind_taint(lambda: state["tainted"], lambda c: asked.append(c) or True)
    assert box.run("a").exit_code == 0 and asked == []
    state["tainted"] = True
    assert box.run("b").exit_code == 0 and asked == ["b"]
    box.bind_taint(lambda: True, None)
    assert box.run("c").exit_code == 1
    assert len(fake.calls) == 2


# -- through the app --------------------------------------------------------------------------


@pytest.fixture
def rig(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, ssh_home: Path) -> Any:
    made = _Rig(tmp_path, monkeypatch)
    yield made
    made.screen.close()
    made.bare.close()
    get_settings.cache_clear()


def _allow(rig: Any, host: str = "web1") -> Any:
    return rig.screen.post("/api/code/environments/ssh-hosts", json={"host": host})


def _make_ssh_env(rig: Any, name: str = "prod") -> str:
    assert _allow(rig).status_code == 200
    response = rig.screen.post(
        "/api/code/environments",
        json={"name": name, "kind": "ssh", "params": {"host": "web1", "remote_path": "/srv/app"}},
    )
    assert response.status_code == 200, response.text
    return next(e["id"] for e in response.json()["environments"] if e["name"] == name)


def test_the_owners_screen_lists_allows_and_forgets_hosts(rig: Any) -> None:
    first = rig.screen.get("/api/code/environments/ssh-hosts").json()
    assert first == {"supported": True, "config_hosts": ["web1", "db.internal", "box"], "allowed": []}
    assert _allow(rig).json()["allowed"] == ["web1"]
    assert _allow(rig).json()["allowed"] == ["web1"]  # allowing twice is one entry
    assert _allow(rig, "ghost").status_code == 400
    assert _allow(rig, "*").status_code == 400
    assert rig.screen.delete("/api/code/environments/ssh-hosts/web1").json()["allowed"] == []
    assert rig.screen.delete("/api/code/environments/ssh-hosts/web1").status_code == 404
    assert {k["kind"] for k in rig.screen.get("/api/code/environments").json()["kinds"]} == {"wsl", "ssh"}


def test_the_bridge_a_paired_device_and_a_bare_caller_cannot_make_an_ssh_environment_or_edit_the_list(
    rig: Any,
) -> None:
    from chimera.api.bridge_routes import VIA_BRIDGE_SCOPE_KEY
    from chimera.api.devices import DEVICE_SCOPE_KEY

    env = {"name": "x", "kind": "ssh", "params": {"host": "web1", "remote_path": "/srv"}}
    assert _allow(rig).status_code == 200
    for client in (
        rig.marked({VIA_BRIDGE_SCOPE_KEY: True}, screen=True),
        rig.marked({DEVICE_SCOPE_KEY: "d1"}, screen=True),
        rig.bare,
    ):
        assert client.post("/api/code/environments", json=env).status_code == 403
        assert client.post("/api/code/environments/ssh-hosts", json={"host": "box"}).status_code == 403
        assert client.delete("/api/code/environments/ssh-hosts/web1").status_code == 403
    for client in (
        rig.marked({VIA_BRIDGE_SCOPE_KEY: True}, screen=True),
        rig.marked({DEVICE_SCOPE_KEY: "d1"}, screen=True),
    ):
        assert client.get("/api/code/environments/ssh-hosts").status_code == 403
    assert rig.screen.get("/api/code/environments/ssh-hosts").json()["allowed"] == ["web1"]
    assert rig.screen.get("/api/code/environments").json()["environments"][1:] == []


def test_the_first_turn_fixes_an_ssh_environment_and_the_turn_gets_its_sandbox(rig: Any) -> None:
    env_id = _make_ssh_env(rig)
    first = rig.turn(rig.screen, environment=env_id)
    assert first.status_code == 200, first.text
    sid = rig.sid(first)
    box = rig.sandboxes[-1]
    assert isinstance(box, SshSandbox) and (box.host, box.remote_path) == ("web1", "/srv/app")
    assert rig.stored(sid)["environment"] == {"id": env_id, "name": "prod", "kind": "ssh"}
    assert "web1" not in json.dumps(rig.stored(sid)["environment"]["name"])
    # The conversation file holds no parameter of the host.
    assert "/srv/app" not in json.dumps(rig.stored(sid).get("environment"))
    assert rig.turn(rig.screen, sid).status_code == 200
    assert isinstance(rig.sandboxes[-1], SshSandbox)
    changed = rig.turn(rig.screen, sid, environment="local")
    assert changed.status_code == 409 and "fixed" in changed.json()["detail"]


def test_a_bridge_turn_cannot_name_an_ssh_environment_and_a_removed_host_ends_the_commands(rig: Any) -> None:
    from chimera.api.bridge_routes import VIA_BRIDGE_SCOPE_KEY

    env_id = _make_ssh_env(rig)
    bridge = rig.marked({VIA_BRIDGE_SCOPE_KEY: True}, screen=True)
    assert rig.turn(bridge, environment=env_id).status_code == 403
    sid = rig.sid(rig.turn(rig.screen, environment=env_id))
    assert isinstance(rig.sandboxes[-1], SshSandbox)
    assert rig.turn(bridge, sid).status_code == 200  # the bridge keeps working in the one it has
    assert isinstance(rig.sandboxes[-1], SshSandbox)
    assert rig.screen.delete("/api/code/environments/ssh-hosts/web1").status_code == 200
    stopped = rig.turn(rig.screen, sid)
    assert stopped.status_code == 409 and "allowed" in stopped.json()["detail"]
    listed = rig.screen.get("/api/code/environments").json()["environments"]
    assert next(e for e in listed if e["id"] == env_id)["available"] is False
