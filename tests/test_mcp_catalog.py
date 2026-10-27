"""The catalogue is a recommendation, so what it must not contain is the point.

Adding an MCP server by hand is a transcription exercise with a silent failure mode — a wrong
argument produces a server that never connects and says nothing about why. A catalogue removes that.
It also introduces a worse failure: a recommendation nobody verified LOOKS verified, and a user has
no way to tell the two apart from the screen.

So most of this file is about what is absent. Three of these guards exist because the research that
built the catalogue nearly shipped each mistake:

* the most-linked ``github-mcp-server`` on npm is published by a third party, not by GitHub;
* the reference TypeScript server is deprecated ("Package no longer supported") and its source
  directory now 404s;
* no classic-PAT scope grants read-only access to private code, so an entry that browses private
  repositories with a classic token can also write to them.
"""

from __future__ import annotations

import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest

from chimera.integrations.mcp_catalog import (
    CATALOG,
    VENDOR_ORGS,
    CatalogEntry,
    _probe_local,
    catalog_as_dicts,
    runner_available,
)


def _entry(entry_id: str):
    achado = next((e for e in CATALOG if e.id == entry_id), None)
    assert achado is not None, f"{entry_id} is not in the catalogue"
    return achado

def test_voicestudio_catalogue_is_a_local_http_server_with_vendor_proof() -> None:
    entry = _entry("voicestudio")
    assert entry.label == "VoiceStudio (local)"
    assert entry.url == "http://127.0.0.1:3900/mcp/"
    assert entry.headers == {"X-OmniVoice-Client-Id": "chimera"}
    assert entry.runner == "" and not entry.command and not entry.args
    assert entry.auth == "local"
    assert entry.exec_tools == ("clone_voice", "design_voice")
    assert entry.repo == "debpalash/VoiceStudio"
    assert entry.official is True
    assert VENDOR_ORGS["voicestudio"] == ("debpalash",)
    assert "CC-BY-NC" in entry.containment and "permission" in entry.containment
    data = next(item for item in catalog_as_dicts() if item["id"] == "voicestudio")
    assert data["url"] == entry.url
    assert data["headers"] == entry.headers
    assert data["vendor_orgs"] == ["debpalash"]
    assert entry.auth in {"oauth", "login", "key", "url", "none", "local"}


@pytest.mark.parametrize(
    "url",
    ["https://127.0.0.1:3900/mcp", "http://example.com/mcp", "http://192.168.1.2/mcp", "http://[2001:db8::1]/mcp"],
)
def test_catalogue_local_url_rejects_non_loopback_or_non_http(url: str) -> None:
    with pytest.raises(ValueError, match="loopback"):
        CatalogEntry(id="bad", label="Bad", summary="", runner="", url=url)


def test_loopback_discovery_probe_uses_local_server_and_reports_availability() -> None:
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"voicestudio.speech.v1": {}}')

        def log_message(self, format: str, *args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        endpoint = f"http://127.0.0.1:{server.server_port}/.well-known/voicestudio-speech"
        assert _probe_local(endpoint)
        server.shutdown()
        thread.join(timeout=2)
        # The answer is cached for three seconds; a clock ten seconds on is past that window, so the
        # probe really runs again and sees the server gone. Nothing here sleeps.
        assert not _probe_local(endpoint, clock=lambda: time.monotonic() + 10.0)
    finally:
        server.server_close()
        thread.join(timeout=2)


def test_every_entry_says_what_bounds_it() -> None:
    """The field the catalogue exists for.

    For most of these servers what limits the damage is the CREDENTIAL — a database grant, a token
    scope — and not the tool list, so a "read-only" badge would say the opposite of the truth. An
    entry with nothing in this field is one the user cannot reason about.
    """
    sem = [e.id for e in CATALOG if len(e.containment.strip()) < 40]

    assert sem == [], f"entries with no account of what limits them: {sem}"


def test_every_entry_names_a_runner_and_a_source() -> None:
    for e in CATALOG:
        assert e.runner or e.url, f"{e.id} does not say how it connects"
        assert e.command or e.url, f"{e.id} has neither a stdio command nor a URL"
        assert e.docs.startswith("https://"), f"{e.id} points at no primary source"


def test_the_github_entry_is_githubs_own() -> None:
    """The trap this guard exists for is specific and live.

    `npx github-mcp-server` resolves to a package published by an unrelated account, and
    `@modelcontextprotocol/server-github` is deprecated with its source archived. Both are what a
    search turns up first, and both would look correct in a config file.
    """
    for entry_id in ("github", "github-binary"):
        e = _entry(entry_id)
        alvo = " ".join([e.command, *e.args])

        assert "npx" not in alvo, f"{entry_id} runs a GitHub server through npm, where GitHub publishes none"
        assert "@modelcontextprotocol/server-github" not in alvo, f"{entry_id} uses the deprecated server"
        assert "ghcr.io/github/" in alvo or e.command == "github-mcp-server", (
            f"{entry_id} does not run GitHub's own build"
        )


def test_github_is_read_only_until_somebody_says_otherwise() -> None:
    """About a third of the server's ~90 tools mutate state, `delete_repository` among them.

    Read-only is the server's strongest control — its own docs call it a strict filter that takes
    precedence over every other setting — so it is the default here, and turning it off has to be a
    deliberate edit rather than something that happens by omission.
    """
    for entry_id in ("github", "github-binary"):
        e = _entry(entry_id)

        assert e.env.get("GITHUB_READ_ONLY") == "1", f"{entry_id} would start able to write"
        # Named explicitly, so that turning read-only off later does not ALSO silently widen the
        # surface from four toolsets to every toolset the server has.
        assert e.env.get("GITHUB_TOOLSETS"), f"{entry_id} leaves the toolset unspecified"


def test_github_never_asks_for_a_token() -> None:
    """The finding that made this entry worth shipping.

    Since v1.10 the server runs the OAuth flow itself and holds the token in memory only. So there
    is no secret for Chimera to collect, store in `mcp.json`, or leak — and an entry that asked for
    a PAT would be giving away that property for nothing.
    """
    for entry_id in ("github", "github-binary"):
        e = _entry(entry_id)

        assert e.secrets == [], f"{entry_id} asks for a secret the server does not need"
        assert "GITHUB_PERSONAL_ACCESS_TOKEN" not in e.env


def test_the_docker_github_entry_binds_the_callback_to_loopback() -> None:
    """`-p 8085:8085` would publish the OAuth callback to every interface.

    The container needs a fixed port because it cannot reach a random one on the host — but bound
    to all interfaces, another machine on the network could receive the redirect.
    """
    e = _entry("github")
    portas = " ".join(e.args)

    assert "127.0.0.1:8085:8085" in portas
    assert " -p 8085:8085" not in f" {portas}"


@pytest.mark.parametrize("entry_id", ["db-sqlite", "db-postgres", "db-mysql", "db-mssql", "db-oracle"])
def test_a_database_entry_admits_it_can_drop_a_table(entry_id: str) -> None:
    """This server has no read-only mode and its engine defaults to AUTOCOMMIT.

    That is the most surprising fact in the catalogue and the one most likely to cost somebody a
    table, so it is stated on every database entry rather than once in a doc nobody opens.
    """
    e = _entry(entry_id)

    assert "DROP" in e.containment, f"{entry_id} does not warn that a DROP would run"
    assert not e.official, f"{entry_id} is a community server and must not read as a vendor's"
    assert [s.key for s in e.secrets] == ["DB_URL"]


def test_a_database_password_travels_in_env_not_in_the_command_line() -> None:
    """Arguments are visible to other processes; environment is not, to the same degree.

    `DB_URL` carries the database password. An entry that put it in `args` would publish it to
    anything that can read the process table.
    """
    for e in CATALOG:
        assert not any("://" in a and "@" in a for a in e.args), (
            f"{e.id} puts a credential-bearing URL on the command line"
        )


def test_availability_is_answered_here_rather_than_guessed() -> None:
    """Only this process can see the machine's PATH.

    An entry offered where its runner is missing is the exact failure the catalogue removes, so the
    answer is computed rather than assumed — and it has to be capable of saying no.
    """
    entradas = catalog_as_dicts()

    assert len(entradas) == len(CATALOG)
    assert all(isinstance(e["available"], bool) for e in entradas)
    assert runner_available("nao-existe-este-executavel-em-lugar-nenhum") is False
    # Guarding the guard, and it took a second attempt: the first version ended in `or True`, which
    # made the whole assertion vacuous — it would have passed against a checker hard-coded to False,
    # which is exactly the bug it was written to catch. `sys.executable` is a path that certainly
    # exists, so `shutil.which` on its own name has to find something.
    import sys
    from pathlib import Path

    assert runner_available(Path(sys.executable).name), (
        "the availability check answers False for the interpreter that is running it"
    )


def test_no_secret_value_is_ever_baked_in() -> None:
    """`env` here holds catalogue DEFAULTS, never credentials — those are asked for at add time.

    A committed file with a token in it is the failure mode this whole module could most easily
    create, since every entry is a config template and a template is a tempting place for one.
    """
    for e in CATALOG:
        for chave, valor in e.env.items():
            assert not any(
                marca in valor for marca in ("ghp_", "github_pat_", "sk_live", "sk_test", "rk_live")
            ), f"{e.id} carries a credential-shaped value in {chave}"


# --- the four entries added with study 29 (P7.3) --------------------------------------------------
#
# Each was checked against the vendor's own page, and two of them against the PACKAGE itself, because
# the README and the code disagreed in small ways that matter (Hostinger's token variable has two
# names; its README says one binary where the package ships twelve).

_NOVAS = ("stripe", "notion", "sentry", "hostinger")
_AUTH = {"oauth", "login", "key", "url", "none", "local"}


def test_every_entry_declares_how_it_signs_in() -> None:
    """An entry with no `secrets` must be a statement, not a gap.

    "Asks for nothing" can mean the server signs in through the browser, reuses a CLI login, or that
    somebody forgot to list the token. The three read the same on screen; `auth` is what tells them
    apart, and it has to agree with `secrets` — a key-based entry that asks for no key is the
    forgotten-token case, and an OAuth one that asks for a key is a leak waiting to be typed in.
    """
    for e in CATALOG:
        assert e.auth in _AUTH, f"{e.id} declares no way of signing in"
        pede = bool(e.secrets)
        assert pede == (e.auth in {"key", "url"}), (
            f"{e.id}: auth={e.auth!r} but secrets={[s.key for s in e.secrets]}"
        )
    assert {d["auth"] for d in catalog_as_dicts()} <= _AUTH


@pytest.mark.parametrize("entry_id", _NOVAS)
def test_a_new_entry_says_what_bounds_it_and_what_it_asks_for(entry_id: str) -> None:
    """The plan's own measure for these four: containment and secrets, each declared.

    A secret must say where it comes from — the URL is half of what makes a catalogue entry better
    than a blank form.
    """
    e = _entry(entry_id)

    assert len(e.containment) >= 120, f"{entry_id} says too little about what limits it"
    assert e.docs.startswith("https://")
    for s in e.secrets:
        assert s.source.startswith("https://"), f"{entry_id}: {s.key} does not say where to get it"
        assert s.hint, f"{entry_id}: {s.key} does not say what to paste"


@pytest.mark.parametrize("entry_id", _NOVAS)
def test_a_new_entry_runs_the_version_that_was_read(entry_id: str) -> None:
    """`@latest` would make the containment sentence about whatever npm serves next week."""
    alvo = " ".join(_entry(entry_id).args)

    assert "@latest" not in alvo
    assert any(re.search(r"@\d+\.\d+\.\d+", a) for a in _entry(entry_id).args), (
        f"{entry_id} runs an unpinned package"
    )


def test_the_stripe_key_never_reaches_the_command_line() -> None:
    """The header is built by mcp-remote from the environment, so `args` holds only a reference.

    Arguments are readable from the process table. The Windows space-mangling workaround (no space
    after the colon, `Bearer <key>` inside the variable) is mcp-remote's documented form, and the
    variable it names has to be one the entry actually asks for — or the header is sent empty.
    """
    e = _entry("stripe")
    header = e.args[e.args.index("--header") + 1]

    assert header == "Authorization:${STRIPE_AUTH_HEADER}"
    assert [s.key for s in e.secrets] == ["STRIPE_AUTH_HEADER"]
    assert not any(marca in " ".join(e.args) for marca in ("Bearer", "rk_", "sk_"))
    # The read-only advice is the containment, so it has to be in the words the user sees.
    assert "read permissions only" in e.containment
    assert "Agent key" in e.secrets[0].hint


def test_the_stripe_entry_says_a_refused_key_opens_a_sign_in_to_decline() -> None:
    """mcp-remote answers a 401 by opening the OAuth consent page - the whole-user grant the key is
    there to avoid - and keeps the tokens on disk. That cannot be switched off from the command, so
    the person has to be told, in the sentence they read before choosing the entry."""
    texto = _entry("stripe").containment

    assert "sign-in" in texto and "decline" in texto, "a refused key's browser sign-in is unmentioned"
    assert "outside mcp.json" in texto, "the grant kept in the bridge's own file is unmentioned"


@pytest.mark.parametrize(
    "valor",
    [
        "rk_test_51Abc",  # the key alone: the most natural paste from Stripe's dashboard
        "Bearer",  # the word alone
        "Bearer ",  # the word and a space, with nothing after
        "Bearer  rk_test_51Abc",  # two spaces: the scheme no longer reads as "Bearer"
        "Bearerrk_test_51Abc",  # no space at all
        "Bearer rk_test 51Abc",  # a key broken by a space is two tokens, not one key
    ],
)
def test_a_stripe_header_without_the_bearer_form_is_refused_by_its_pattern(valor: str) -> None:
    """"Not empty" was the whole guard, and a key pasted on its own passes it.

    The header goes out as ``Authorization:<value>``, so a value without the scheme is a header
    Stripe refuses - and mcp-remote answers a refusal by opening the whole-user OAuth page. The
    pattern is what the screen enforces; these are the values it must refuse.
    """
    padrao = _entry("stripe").secrets[0].pattern

    assert padrao, "the Stripe header declares no form, so the screen only checks it is not empty"
    assert re.search(padrao, valor) is None, f"{valor!r} would be saved and sent to Stripe"


def test_a_stripe_header_in_the_bearer_form_is_accepted_and_the_hint_fits_its_own_pattern() -> None:
    """The guard must not refuse the value it asks for - including the example in its own hint."""
    segredo = _entry("stripe").secrets[0]

    assert re.search(segredo.pattern, "Bearer rk_test_51Abc") is not None
    assert re.search(segredo.pattern, segredo.hint.split(" (")[0]) is not None


def test_every_secret_pattern_is_anchored_and_reaches_the_screen() -> None:
    """A pattern is matched with ``RegExp.test`` in the browser, which looks for it ANYWHERE in the
    value: an unanchored one accepts "rk_x Bearer y". And one left out of the API answer is a guard
    the screen never hears about."""
    por_id = {d["id"]: d for d in catalog_as_dicts()}
    for e in CATALOG:
        for s in e.secrets:
            if s.pattern:
                re.compile(s.pattern)
                assert s.pattern.startswith("^") and s.pattern.endswith("$"), (e.id, s.key)
            secrets = por_id[e.id]["secrets"]
            assert isinstance(secrets, list)
            enviados = {x["key"]: x for x in secrets}
            assert enviados[s.key]["pattern"] == s.pattern, (e.id, s.key)


def test_notion_does_not_use_its_unmaintained_local_server() -> None:
    """Notion's own README: the local server is "no longer actively maintained" — the GitHub trap."""
    alvo = " ".join([_entry("notion").command, *_entry("notion").args])

    assert "@notionhq/notion-mcp-server" not in alvo
    assert "https://mcp.notion.com/mcp" in alvo


def test_sentry_starts_with_only_the_read_only_skill() -> None:
    """The default skill set includes `seer` (Sentry's AI over your issues); triage and project
    management mutate. Only `inspect` is turned on, and the token travels in the environment."""
    e = _entry("sentry")

    assert "--skills=inspect" in e.args
    assert not any(a.startswith("--access-token") for a in e.args)
    assert [s.key for s in e.secrets] == ["SENTRY_ACCESS_TOKEN"]


def test_hostinger_runs_one_api_group_and_admits_it_can_write() -> None:
    """The all-groups binary is 403 operations, billing among them, and nothing is read-only.

    The group binary is the only narrowing the package offers, so it is the default — and the
    containment still has to say that most of what is left changes something.
    """
    e = _entry("hostinger")

    assert "hostinger-vps-mcp" in e.args
    assert "hostinger-api-mcp" not in e.args
    assert "no read-only mode" in e.containment
    assert "whole account" in e.containment


_REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


def test_every_entry_names_the_repository_that_publishes_it() -> None:
    """The owner of this repository is half of the "official vendor" proof (study 04, §3.2)."""
    sem = [e.id for e in CATALOG if not _REPO.match(e.repo)]

    assert sem == [], f"entries without an owner/name repository: {sem}"


def test_an_official_entry_is_published_by_the_vendors_own_organisation() -> None:
    """`official` alone is the catalogue's claim; the badge also needs a fact anyone can check.

    The card shows "Official vendor" only when the entry is official AND its repository's owner is one
    of the vendor's organisations. This is the build-time half: an official entry whose repository is
    owned by anybody else fails here, before a screen could show the badge for it.
    """
    sem_prova = [
        (e.id, e.repo)
        for e in CATALOG
        if e.official and e.repo.split("/")[0].lower() not in {o.lower() for o in VENDOR_ORGS.get(e.id, ())}
    ]

    assert sem_prova == [], f"official entries not published by the vendor's organisation: {sem_prova}"


def test_a_community_entry_cannot_borrow_a_vendors_organisation() -> None:
    """The other direction: an entry the catalogue calls community has no vendor row to match."""
    comunidade = [e.id for e in CATALOG if not e.official and e.id in VENDOR_ORGS]

    assert comunidade == [], f"community entries listed with a vendor organisation: {comunidade}"
    assert set(VENDOR_ORGS) <= {e.id for e in CATALOG}, "a vendor row names an entry that does not exist"


def test_the_api_carries_the_proof_the_badge_reads() -> None:
    por_id = {d["id"]: d for d in catalog_as_dicts()}

    assert por_id["github"]["repo"] == "github/github-mcp-server"
    assert por_id["github"]["vendor_orgs"] == ["github"]
    assert por_id["db-postgres"]["vendor_orgs"] == []
    assert por_id["db-postgres"]["official"] is False


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost./mcp",
        "http://0x7f000001/mcp",
        "http://127.0.0.1.nip.io:3900/mcp",
        "http://user:secret@127.0.0.1:3900/mcp",
        "http://127.1/mcp",
    ],
)
def test_a_lookalike_or_credentialed_host_is_not_loopback(url: str) -> None:
    # Each of these resolves, or reads, as loopback to a careless check and reaches somewhere else
    # (or carries a credential) in practice. Only the literal loopback address and ``localhost`` pass.
    with pytest.raises(ValueError, match="loopback"):
        CatalogEntry(id="bad", label="Bad", summary="", runner="", url=url)


def _serve(handler_cls: type[BaseHTTPRequestHandler]) -> tuple[ThreadingHTTPServer, str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
    Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_port}/.well-known/voicestudio-speech"


def test_a_redirect_is_not_followed_and_reads_as_unavailable() -> None:
    hits: list[str] = []

    class Redirects(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            hits.append(self.path)
            if self.path.startswith("/.well-known"):
                self.send_response(302)
                self.send_header("Location", "/elsewhere")
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"voicestudio.speech.v1": {}}')

        def log_message(self, format: str, *args: object) -> None:
            return

    server, endpoint = _serve(Redirects)
    try:
        assert _probe_local(endpoint, clock=lambda: 0.0) is False
        assert hits == ["/.well-known/voicestudio-speech"], "the redirect target must not be requested"
    finally:
        server.shutdown()
        server.server_close()


def test_the_probe_ignores_a_proxy_in_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    class Answers(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"voicestudio.speech.v1": {}}')

        def log_message(self, format: str, *args: object) -> None:
            return

    server, endpoint = _serve(Answers)
    # A proxy that cannot answer. A probe that honoured it would fail for a reason unrelated to
    # whether the local service is up, and would send a loopback request to someone else's machine.
    for name in ("HTTP_PROXY", "http_proxy"):
        monkeypatch.setenv(name, "http://127.0.0.1:9")
    try:
        assert _probe_local(endpoint, clock=lambda: 0.0) is True
    finally:
        server.shutdown()
        server.server_close()


def test_a_probe_answer_is_reused_for_three_seconds_then_asked_again() -> None:
    calls: list[str] = []
    answers = iter([True, False])

    def fetch(url: str) -> bool:
        calls.append(url)
        return next(answers)

    url = "http://127.0.0.1:1/cache-window"
    now = [100.0]
    assert _probe_local(url, clock=lambda: now[0], fetch=fetch) is True
    now[0] = 102.9
    assert _probe_local(url, clock=lambda: now[0], fetch=fetch) is True
    assert len(calls) == 1, "inside the window the cached answer is used"
    now[0] = 103.0
    assert _probe_local(url, clock=lambda: now[0], fetch=fetch) is False
    assert len(calls) == 2


def test_a_probe_has_a_total_deadline_however_slowly_the_server_answers() -> None:
    release = threading.Event()

    def stalls(url: str) -> bool:
        release.wait(5.0)
        return True

    started = time.monotonic()
    result = _probe_local("http://127.0.0.1:1/deadline", clock=lambda: 0.0, fetch=stalls)
    elapsed = time.monotonic() - started
    release.set()
    assert result is False
    assert elapsed < 2.0, f"the probe held the caller for {elapsed:.2f}s"


def test_the_voicestudio_entry_offers_its_install_page_from_the_catalogue() -> None:
    entry = _entry("voicestudio")
    assert entry.install_url == "https://github.com/debpalash/VoiceStudio/releases/latest"
    data = next(item for item in catalog_as_dicts() if item["id"] == "voicestudio")
    assert data["install_url"] == entry.install_url
    assert data["discovery_url"] == entry.discovery_url
    # A stdio entry has nothing to install from the catalogue's point of view, so it carries none.
    assert all(item["install_url"] == "" for item in catalog_as_dicts() if item["id"] != "voicestudio")

