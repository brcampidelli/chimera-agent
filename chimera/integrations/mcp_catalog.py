"""A curated catalogue of MCP servers, so connecting one is a choice rather than a transcription.

Adding an MCP server means typing a command, an argument list and a set of environment variable
names correctly, from memory or from a vendor page. That is a transcription exercise with a silent
failure mode — a wrong argument produces a server that simply never connects — and it is the reason
the MCP screen has stayed empty.

**Every entry here was checked against a primary source**, and the ones that could not be are not
here. That rule cost more than it sounds: the most-linked GitHub MCP package on npm is not GitHub's,
the reference TypeScript one is deprecated, and several services in the same space have no
first-party server at all. A catalogue is a recommendation, and a recommendation nobody verified is
worse than an empty screen, because it looks like it was verified.

Three properties every entry carries, because each is something a user cannot see from the outside:

* ``runner`` — the executable the entry needs (``docker``, ``uvx``, ``npx``, or a binary on PATH).
  The screen checks whether it exists BEFORE offering the entry, so a missing runner reads as
  "install this first" rather than as a server that mysteriously fails to start.
* ``secrets`` — env vars the user must supply, with where to get them. Values live in ``mcp.json``
  in plain text, the same as a ``.env``; that is stated on the screen rather than implied.
* ``containment`` — what actually limits the damage. This is the field the catalogue exists to be
  honest about, and it is usually not what a "read-only" badge would suggest: for most servers the
  limit is the CREDENTIAL (a database grant, a restricted key, a token scope), not the tool list.

Chimera's MCP client speaks **stdio only** (:class:`~chimera.integrations.mcp_client.StdioMCPSession`),
so a server documented as a remote HTTPS URL can only appear here through a stdio bridge, and the
entry says so instead of quietly wrapping it.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field


@dataclass(frozen=True)
class CatalogSecret:
    """One environment variable the user has to supply, and where they get it."""

    key: str
    #: What to paste, in the user's terms — not the variable's name restated.
    hint: str
    #: Where it comes from. A URL when there is a page for it, else a command.
    source: str = ""
    #: The FORM the value must have, as a regular expression the whole value has to match, or empty
    #: for "anything not empty". The screen will not save a value that fails it. Written in the
    #: subset that means the same in Python's ``re`` and in a JavaScript ``RegExp``, because the
    #: browser is where it is enforced and the tests here are where it is checked.
    #:
    #: It exists because "not empty" turned out to be the wrong guard for a header: the Stripe entry
    #: needs ``Bearer <key>``, and the most natural paste is the key alone, straight from Stripe's
    #: dashboard. That passes "not empty", goes out as a header with no scheme, is refused, and the
    #: bridge answers the refusal by opening the whole-user sign-in the key was there to avoid.
    pattern: str = ""


@dataclass(frozen=True)
class CatalogEntry:
    """A verified way to run one MCP server."""

    id: str
    label: str
    #: One sentence: what the agent gains. Not marketing.
    summary: str
    #: The executable that has to exist. Checked at read time, never assumed.
    runner: str
    command: str
    args: list[str] = field(default_factory=list)
    #: Env vars with a fixed value — defaults the catalogue chooses, not secrets.
    env: dict[str, str] = field(default_factory=dict)
    secrets: list[CatalogSecret] = field(default_factory=list)
    #: What actually bounds the damage if the model misbehaves or is injected into.
    containment: str = ""
    #: First-party from the vendor, or community. Never guessed.
    official: bool = True
    docs: str = ""
    #: How the server proves who it is acting as, so an entry with no ``secrets`` is a DECLARATION
    #: rather than an omission: ``oauth`` (a browser sign-in the server or its bridge keeps),
    #: ``login`` (reuses a CLI that is already signed in), ``key`` (a token the user pastes into
    #: ``secrets``) or ``url`` (a connection string in ``secrets`` that carries the credential).
    #: Without it, "asks for nothing" and "somebody forgot to list the token" read the same.
    auth: str = "oauth"


#: The default GitHub entry runs the official server through Docker, and the reason is the strongest
#: fact this catalogue found: since v1.10, the server performs the OAuth+PKCE flow ITSELF and keeps
#: the resulting token in memory only, never on disk. Chimera therefore never sees a GitHub token —
#: no PAT to paste, nothing to store, nothing to leak out of `mcp.json`.
#:
#: Docker needs a FIXED callback port because a container cannot reach a random loopback port on the
#: host, and 8085 is the port the official app registered. Published to 127.0.0.1 deliberately: bound
#: to all interfaces, another machine on the network could receive the OAuth redirect.
_GITHUB_DOCKER = CatalogEntry(
    id="github",
    label="GitHub",
    summary="Read repositories, issues and pull requests — the code and the conversation around it.",
    runner="docker",
    command="docker",
    args=[
        "run", "-i", "--rm",
        "-p", "127.0.0.1:8085:8085",
        "-e", "GITHUB_OAUTH_CALLBACK_PORT",
        "-e", "GITHUB_READ_ONLY",
        "-e", "GITHUB_TOOLSETS",
        "ghcr.io/github/github-mcp-server",
    ],
    env={
        "GITHUB_OAUTH_CALLBACK_PORT": "8085",
        # Read-only by default, and this is a deliberate default rather than a timid one. Of the
        # server's ~90 tools roughly a third mutate state, including `delete_repository` and
        # `actions_run_trigger`. Its own documentation calls read-only "a strict security filter
        # that takes precedence over any other configuration".
        "GITHUB_READ_ONLY": "1",
        # The server's own default set. Named explicitly so that turning read-only OFF does not
        # silently also widen the surface to every toolset at once.
        "GITHUB_TOOLSETS": "context,repos,issues,pull_requests",
    },
    containment=(
        "Read-only is on, which the server enforces above every other setting. Nothing is stored: "
        "the OAuth token lives in the server's memory and never reaches disk or Chimera."
    ),
    docs="https://github.com/github/github-mcp-server",
)

#: Same server, no Docker. The native binary needs no fixed port — with no browser it falls back to
#: GitHub's device-code flow on its own. Offered second only because it has to be installed first;
#: for anyone who has it, this is the lighter path.
_GITHUB_BINARY = CatalogEntry(
    id="github-binary",
    label="GitHub (installed binary)",
    summary="The same GitHub server, run directly instead of through Docker.",
    runner="github-mcp-server",
    command="github-mcp-server",
    args=["stdio"],
    env={"GITHUB_READ_ONLY": "1", "GITHUB_TOOLSETS": "context,repos,issues,pull_requests"},
    containment=(
        "Read-only is on. The browser sign-in happens in the server, so the token never reaches "
        "Chimera; with no browser available it falls back to a device code."
    ),
    docs="https://github.com/github/github-mcp-server/blob/main/docs/oauth-login.md",
)

#: One server, seven databases, differing only in the driver pulled in and the URL scheme. Written
#: as a loop rather than seven near-identical literals, because the thing that varies is the thing a
#: reader needs to see.
_ALCHEMY_VERSION = "2026.8.1.2602"
_ALCHEMY_DBS: tuple[tuple[str, str, str, str], ...] = (
    ("sqlite", "SQLite", "", "sqlite:////caminho/absoluto/base.db"),
    ("postgres", "PostgreSQL", "psycopg2-binary", "postgresql://user:senha@localhost/base"),
    ("mysql", "MySQL / MariaDB", "pymysql", "mysql+pymysql://user:senha@localhost/base"),
    ("mssql", "SQL Server", "pymssql", "mssql+pymssql://user:senha@localhost/base"),
    ("oracle", "Oracle", "oracledb", "oracle+oracledb://user:senha@localhost/base"),
)

#: Said once, on every database entry, because it is the single most surprising fact in this file
#: and the one most likely to cost somebody a table. There is no read-only mode: the server runs
#: whatever SQL it is given, and its engine defaults to AUTOCOMMIT, so there is no transaction to
#: roll back. The credential is the boundary — the whole boundary.
_ALCHEMY_CONTAINMENT = (
    "This server has NO read-only mode and commits every statement immediately — a DROP runs. "
    "What limits it is the database user in the connection URL, so point it at one with only the "
    "grants you are willing to lose."
)


def _alchemy(db_id: str, label: str, driver: str, example: str) -> CatalogEntry:
    args = ["--from", f"mcp-alchemy=={_ALCHEMY_VERSION}"]
    if driver:
        args += ["--with", driver]
    args += ["--refresh-package", "mcp-alchemy", "mcp-alchemy"]
    return CatalogEntry(
        id=f"db-{db_id}",
        label=label,
        summary=f"Inspect and query a {label} database: table names, schemas, and SQL.",
        runner="uvx",
        command="uvx",
        args=args,
        secrets=[CatalogSecret(key="DB_URL", hint=example, source="")],
        auth="url",
        containment=_ALCHEMY_CONTAINMENT,
        # Community, by one maintainer. Said out loud: it is a good server and it is not a vendor's.
        official=False,
        docs="https://github.com/runekaagaard/mcp-alchemy",
    )


#: The other entry that needs no secret, for the same reason and by a different route: it reuses the
#: Firebase CLI's own credentials, so whoever ran `firebase login` is who the agent acts as. Nothing
#: to paste, nothing stored here.
#:
#: The subcommand is `mcp`. `experimental:mcp` still resolves as an alias, which is exactly why it
#: is worth writing the canonical one down — an alias that works is an alias nobody notices is stale.
_FIREBASE = CatalogEntry(
    id="firebase",
    label="Firebase",
    summary="Query Firestore, Auth, Storage and the rest of a Firebase project.",
    runner="npx",
    command="npx",
    args=["-y", "firebase-tools@latest", "mcp"],
    auth="login",
    containment=(
        "It acts as whoever ran `firebase login` on this machine, so the account's own permissions "
        "are the boundary. There is no read-only switch: the per-tool read-only marks are hints to "
        "the client, not something the server enforces. Narrow it with `--only`."
    ),
    docs="https://firebase.google.com/docs/cli/mcp-server",
)


#: The only one of the design/backend servers surveyed that fits here without a compromise, and the
#: reason is worth writing down: it is the sole server found with server-ENFORCED scoping rather
#: than advisory hints. `read_only=true` is in the URL by default, alongside `features` narrowed to
#: reading — widening it is an edit somebody makes on purpose.
#:
#: It speaks HTTP, and Chimera's client speaks stdio, so it runs through `mcp-remote` — a bridge,
#: named here rather than hidden, because it is a third-party package standing between the app and
#: the server. Its OAuth uses dynamic client registration, so there is still no key to paste.
_SUPABASE = CatalogEntry(
    id="supabase",
    label="Supabase",
    summary="Read a Supabase project: schema, data, edge functions and its documentation.",
    runner="npx",
    command="npx",
    args=[
        "-y", "mcp-remote",
        "https://mcp.supabase.com/mcp?read_only=true&features=database,docs",
    ],
    containment=(
        "Read-only and narrowed to database and docs, both enforced by Supabase rather than by a "
        "hint the client may ignore. Sign-in happens in the browser, so there is no key to store. "
        "It reaches the server through the third-party `mcp-remote` bridge."
    ),
    docs="https://supabase.com/docs/guides/ai-tools/mcp",
)


#: The four below were added together and are pinned to the version that was READ, not to
#: ``@latest``: each package was unpacked or its README read at that version, and a pin is the only
#: way the sentence in ``containment`` stays about the code that actually runs. Bumping one is an
#: edit that should come with re-reading it.
_MCP_REMOTE = "mcp-remote@0.14.3"

#: Stripe's documented server is REMOTE only (``mcp.stripe.com``); the old local ``@stripe/mcp``
#: package is not what its docs describe any more. So this goes through the same named bridge as
#: Supabase, authenticated with a key in a header rather than OAuth, because a key can be narrowed
#: and an OAuth grant here is the whole Stripe user.
#:
#: The header is ``Authorization:${STRIPE_AUTH_HEADER}`` with NO space after the colon, and the
#: secret holds ``Bearer <key>``. That shape is mcp-remote's own workaround: on Windows an argument
#: with a space is re-split on its way through ``npx.cmd``, and the header arrives mangled. The
#: ``${VAR}`` is expanded by mcp-remote from its environment, so the key never sits in ``args`` —
#: arguments are visible in the process table, the environment far less so.
#:
#: Stripe announced that from 31 October 2026 its MCP accepts only OAuth or keys carrying the Agent
#: tag; a plain restricted or secret key then answers 401. The hint asks for an Agent key, which is
#: also the one Stripe lets you create with only the permissions the client needs.
#:
#: What a refused key does is the bridge's choice, not Stripe's, and it is the opposite of the one
#: this entry makes: mcp-remote 0.14.3 treats any 401 as "Authentication required", runs its OAuth
#: flow and opens the browser on Stripe's consent page - the whole-user grant the key is here to
#: avoid - and keeps the tokens under ``~/.mcp-auth``, hashed on the header TEMPLATE, so they would
#: serve any later key. There is no flag to turn that off, so the screen refuses to save the entry
#: with the header empty OR not of the form ``Bearer <key>`` (the secret's ``pattern``; a key pasted
#: on its own is refused exactly like an empty one), and the containment tells the person to decline
#: the page if it appears.
_STRIPE = CatalogEntry(
    id="stripe",
    label="Stripe",
    summary="Read customers, payments, subscriptions and invoices, and search Stripe's documentation.",
    runner="npx",
    command="npx",
    args=[
        "-y", _MCP_REMOTE,
        "https://mcp.stripe.com",
        "--header", "Authorization:${STRIPE_AUTH_HEADER}",
    ],
    secrets=[
        CatalogSecret(
            key="STRIPE_AUTH_HEADER",
            hint="Bearer rk_… (an Agent key with read permissions only)",
            source="https://docs.stripe.com/keys#agent-keys",
            # The word, ONE space, then a key with no whitespace in it. The key alone, the word
            # alone, and "Bearer " with nothing after it are each a header Stripe refuses - and a
            # refusal is what opens the browser sign-in.
            pattern=r"^Bearer \S+$",
        )
    ],
    auth="key",
    containment=(
        "The key is the boundary: the server has one tool that can call any write method of the "
        "Stripe API, so create an Agent key with read permissions only and a write is refused by "
        "Stripe rather than by a hint. Stripe also asks a human to confirm some writes, such as "
        "refunds and outbound payments, through a link. STRIPE_AUTH_HEADER takes the word Bearer, "
        "a space, then the key. It reaches mcp.stripe.com through the third-party mcp-remote bridge. "
        "If Stripe refuses the key - an empty one, or from 31 Oct 2026 one without the Agent tag - "
        "the bridge opens Stripe's sign-in in the browser instead: decline it, because that grant is "
        "your whole Stripe user and the bridge keeps it in a file on this computer, outside mcp.json."
    ),
    docs="https://docs.stripe.com/mcp",
)

#: Notion's own local server (``@notionhq/notion-mcp-server``) says in its README that it is "no
#: longer actively maintained" and points at the remote one — the same trap as the deprecated GitHub
#: package, so it is not offered. Notion documents exactly this command for stdio-only clients.
_NOTION = CatalogEntry(
    id="notion",
    label="Notion",
    summary="Search and read the pages and databases of a Notion workspace.",
    runner="npx",
    command="npx",
    args=["-y", _MCP_REMOTE, "https://mcp.notion.com/mcp"],
    containment=(
        "It acts as you: what it can reach is decided by your permissions in the workspace you sign "
        "in to, and Notion documents no read-only switch for it, so it can edit what you can edit. "
        "Sign-in happens in the browser, so there is no key to store. It goes through the "
        "third-party mcp-remote bridge, because Notion's own local server is no longer maintained."
    ),
    docs="https://developers.notion.com/docs/get-started-with-mcp",
)

#: Sentry's skills are enforced by the server — a skill left out is a set of tools never registered,
#: not a hint. ``inspect`` is described by Sentry as "read-only access to core Sentry data", and it is
#: the only one turned on here: the default set also includes ``seer``, which runs Sentry's own AI
#: on your issues, and ``triage``/``project-management`` mutate. The token travels in the
#: environment (the docs' example puts it in ``--access-token``, i.e. on the command line).
_SENTRY = CatalogEntry(
    id="sentry",
    label="Sentry",
    summary="Read issues, events, traces and releases from Sentry to investigate an error.",
    runner="npx",
    command="npx",
    args=["-y", "@sentry/mcp-server@0.42.0", "--skills=inspect"],
    secrets=[
        CatalogSecret(
            key="SENTRY_ACCESS_TOKEN",
            hint="a Sentry user auth token",
            source="https://sentry.io/settings/account/api/auth-tokens/",
        )
    ],
    auth="key",
    containment=(
        "Narrowed to the read-only inspect skill, which the server enforces by not registering the "
        "other tools. The token is the outer boundary: Sentry's docs ask for write scopes "
        "(project:write, team:write, event:write), so widening --skills turns those writes on. "
        "Needs Node 22.13 or newer."
    ),
    docs="https://github.com/getsentry/sentry-mcp",
)

#: Hostinger's package ships one binary per API group, and that is the only narrowing it offers:
#: there is no read-only mode, and the credential reaches the whole account. The all-groups binary
#: is 403 operations, billing and domain transfers among them; this entry runs the VPS group alone.
#: Counted in the 2.7.0 package rather than taken from the README: 64 operations, 21 of them GET.
#:
#: With no token set, the server runs its own OAuth sign-in and keeps the result in its OWN file
#: (``%APPDATA%/hostinger-mcp/credentials.json``, ``~/.config/hostinger-mcp/`` elsewhere) — on disk,
#: unlike GitHub's, and said so. A ``HOSTINGER_API_TOKEN`` overrides it; it is not asked for here
#: because the sign-in needs nothing pasted into ``mcp.json``.
_HOSTINGER = CatalogEntry(
    id="hostinger",
    label="Hostinger (VPS)",
    summary="See and manage Hostinger VPS machines: state, backups, snapshots, firewalls and Docker.",
    runner="npx",
    command="npx",
    args=["-y", "--package=@hostinger/mcp@2.7.0", "hostinger-vps-mcp"],
    containment=(
        "There is no read-only mode: 43 of the 64 VPS operations change something — restoring a "
        "backup or snapshot, deleting a snapshot or a firewall, stopping a container. Only the VPS "
        "group runs (billing, domains and DNS are left out), but the sign-in reaches the whole "
        "account, and the server keeps it in its own file on this machine."
    ),
    docs="https://github.com/hostinger/api-mcp-server",
)


CATALOG: tuple[CatalogEntry, ...] = (
    _GITHUB_DOCKER,
    _GITHUB_BINARY,
    _FIREBASE,
    _SUPABASE,
    _STRIPE,
    _NOTION,
    _SENTRY,
    _HOSTINGER,
    *(_alchemy(*db) for db in _ALCHEMY_DBS),
)


def runner_available(runner: str) -> bool:
    """Whether the executable an entry needs is on PATH.

    Asked at read time rather than cached: a user who installs Docker after opening the screen
    should not have to know that a restart is what makes the entry work.
    """
    return shutil.which(runner) is not None


def catalog_as_dicts() -> list[dict[str, object]]:
    """The catalogue for the API, with runner availability resolved.

    ``available`` is computed HERE rather than in the browser, because only this process can see the
    machine's PATH — and an entry offered on a machine that cannot run it is the failure this
    catalogue exists to remove.
    """
    return [
        {
            "id": e.id,
            "label": e.label,
            "summary": e.summary,
            "runner": e.runner,
            "available": runner_available(e.runner),
            "command": e.command,
            "args": list(e.args),
            "env": dict(e.env),
            "secrets": [
                {"key": s.key, "hint": s.hint, "source": s.source, "pattern": s.pattern}
                for s in e.secrets
            ],
            "containment": e.containment,
            "official": e.official,
            "docs": e.docs,
            "auth": e.auth,
        }
        for e in CATALOG
    ]
