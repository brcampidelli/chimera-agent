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

import pytest

from chimera.integrations.mcp_catalog import CATALOG, catalog_as_dicts, runner_available


def _entry(entry_id: str):
    achado = next((e for e in CATALOG if e.id == entry_id), None)
    assert achado is not None, f"{entry_id} is not in the catalogue"
    return achado


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
        assert e.runner, f"{e.id} does not say what it needs installed"
        assert e.command, f"{e.id} has no command"
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
_AUTH = {"oauth", "login", "key", "url"}


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
