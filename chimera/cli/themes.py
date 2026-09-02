"""How the command reference is grouped, and the guarantee that nothing falls out of it.

`--help` is alphabetical, which is the right answer for someone who already knows the name and
useless for someone who does not. These are the themes the documentation site renders.

**This lived on the site until 0.48.0, and that is why it kept going stale.** The check that every
command lands in a theme could only run where the list was, so it ran at *deploy* time — after the
release. Three times it caught the same omission (`sessions`, then the installable skills
catalogue, then `approve` and `secrets`), and all three times the download page sat on the previous
version until somebody read a red deploy to find out why. A gate that fires after the thing it
guards has already shipped is a report, not a gate.

The list belongs where the commands are. Adding a command and forgetting the reference now fails on
the pull request that adds it.

The `key` of each theme is an identifier the site translates; nothing here reads it. It is kept
verbatim rather than renamed so the move is a move and not a rename with a mapping layer to get
wrong.
"""

from __future__ import annotations

from typing import NamedTuple


class Theme(NamedTuple):
    """One group of the reference. `key` is the site's translation key for its title."""

    key: str
    commands: tuple[str, ...]


THEMES: tuple[Theme, ...] = (
    Theme(
        "cli.themeSetup",
        (
            "init",
            "doctor",
            "version",
            "features",
            "maturity",
            "migrate",
            "models",
            # Provider keys in the OS keychain instead of a `.env`, shipped in 0.48.0. Setup,
            # because it is where a person puts a key before anything else works.
            "secrets",
        ),
    ),
    Theme(
        "cli.themeWork",
        (
            "chat",
            # Shipped with the persistent terminal conversation and never listed, so the reference
            # described a CLI with one fewer command than the CLI has.
            "sessions",
            "tui",
            "assist",
            "run",
            "agent",
            "deliver",
            "solve",
            "solve-batch",
            "crew",
            "crew-isolated",
            "lifecycle",
            "meta",
            "explore",
            # Searching a repository by what the code DOES rather than by the string it contains.
            # `chimera/rag/` had been in the tree since 0.44.0 with no entrance; `find` is it.
            "find",
            "workflow",
            "drift",
            "scenarios",
        ),
    ),
    Theme("cli.themeFusion", ("fuse", "fusion-receipts", "orchestrate", "brief", "delegations")),
    Theme("cli.themeMemory", ("memory", "profile", "playbook", "skills", "tools")),
    Theme(
        "cli.themeSkills",
        (
            # Shipped with the curated library and never listed, so the reference was one command
            # short — and the site's own test failed on it every run, which means the site stopped
            # deploying too. The gate was right; nobody was reading it.
            "skills-library",
            "skills-pending",
            "skills-stats",
            "skills-approve",
            "skills-export",
            "skills-import",
            "skills-retire",
            "skills-lifecycle",
            "skills-evolve",
            # The installable catalogue, shipped in 0.48.0rc10. Same lesson one release later:
            # the gate caught it, the deploy went red, and the download page sat on the previous
            # version until somebody read why. Browse, fetch, switch on, switch off, remove — in
            # the order a person meets them.
            "skills-catalog",
            "skills-install",
            "skills-bundles",
            "skills-bundle-enable",
            "skills-bundle-disable",
            "skills-uninstall",
            "evolve",
        ),
    ),
    Theme("cli.themeAutomation", ("cron", "kanban", "project", "agents")),
    Theme(
        "cli.themeServe",
        # `acp` is the agent side of the Agent Client Protocol — the mirror of the client half the
        # Code screen uses. It belongs beside the other ways something outside reaches the agent.
        ("serve", "app", "mcp", "a2a-card", "acp"),
    ),
    Theme(
        "cli.themeSafety",
        # `approve` answers a decision the kernel is waiting on, from anywhere — shipped in 0.48.0,
        # because without a terminal the approval gate had been collapsing to a refusal. It belongs
        # with the kernel it answers to, not with setup.
        ("guard", "redteam", "approve"),
    ),
    Theme(
        "cli.themeBench",
        (
            "bench",
            # The rulers the agent's own comments tell you to use, and could not reach: the RAG
            # recall bench and the reranker A/B had no export and no caller. `measure` is how they
            # run. Named apart from `bench` because mounting it there shadowed the existing command.
            "measure",
            "bench-compare",
            "swe-bench-compare",
            "fusion-bench",
            "cascade-bench",
            "hierarchy-bench",
            "skillcard-bench",
            "schema-bench",
            "sandbox-bench",
            "memory-bench",
            "memory-poison",
            "probe-select",
            "transfer-gate",
            "evoclaw",
            "rubric-grade",
            "context-curve",
        ),
    ),
    Theme("cli.themeFun", ("pet",)),
)


def _visible_top_level() -> list[str]:
    """The commands a user can reach, read from the CLI itself rather than from the snapshot.

    The snapshot is a committed file that can be stale; the app object cannot be. Reading the app
    is what makes this gate fire on the pull request that adds a command, which is the entire point
    of moving it here.
    """
    import typer.main

    from chimera.cli.main import app

    root = typer.main.get_command(app)
    commands = getattr(root, "commands", None)
    if not isinstance(commands, dict):  # pragma: no cover - the CLI is always a group
        raise TypeError("the Chimera CLI is expected to expose subcommands")
    return sorted(name for name, cmd in commands.items() if not getattr(cmd, "hidden", False))


def unthemed() -> list[str]:
    """Visible commands no theme claims. A release with any of these has an incomplete reference."""
    claimed = {name for theme in THEMES for name in theme.commands}
    return [name for name in _visible_top_level() if name not in claimed]


def phantoms() -> list[str]:
    """Theme entries naming a command that does not exist — a reference to a removed command."""
    real = set(_visible_top_level())
    return [name for theme in THEMES for name in theme.commands if name not in real]


def build() -> list[dict[str, object]]:
    """The themes as data, for the documentation site to render."""
    return [{"key": theme.key, "commands": list(theme.commands)} for theme in THEMES]
