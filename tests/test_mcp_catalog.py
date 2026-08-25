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
