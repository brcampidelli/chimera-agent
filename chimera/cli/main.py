"""Chimera command-line interface (CLI-first).

Commands:
  version / doctor / models     status & configuration
  run PROMPT                     single-shot Tier-1 completion
  fuse PROMPT                    LLM-Fusion (panel -> judge -> synthesizer)
  agent TASK                     ReAct agent loop with native tools
  solve TASK                     Tier-2 autonomous (plan + verify-or-revert)
  solve-batch TASKS...           Tier-3: solve tasks in parallel, each in its own worktree
  explore QUERY                  locate code via the isolated Context Explorer subagent
  crew-isolated TASK -W ...       tool-using workers split one task in parallel worktrees
  tools / skills                 list native tools / built-in skills
  skills library [NAME]          browse the curated skill cards that ship in the box
  memory ...                     curated long-term memory (add/search/list)
  cron ...                       scheduled jobs (add/list/remove/enable/disable/learn)
  migrate SOURCE DIR             import config/skills/memory from another agent
  bench [RULER]                  continuous-evolution benchmark, and every other ruler

Layout (S30-70). This file used to hold every command — 10,486 lines. The commands now live in
``chimera/cli/commands/``, one module per area, and this module is what it always was to everything
outside it: the ``chimera`` entry point (``chimera.cli.main:app``) and the place other code and the
tests import CLI names from. Importing the area modules below is what registers their commands on
``app``; every name they define is re-exported here (``__all__``) so no import elsewhere changed.

**A patch on this module would no longer reach the code.** A command looks a helper up in the module
it lives in, so ``monkeypatch.setattr("chimera.cli.main._memory_manager", fake)`` would replace a
name nothing reads, and the test would go on exercising the real helper — passing, and testing
nothing. Rather than let that fail silently, this module refuses to have a re-exported name
replaced and says where to patch instead (see ``_ReexportsAreReadOnly``).
"""

from __future__ import annotations

import importlib
import sys
import types
from typing import Any

# Registration order is `--help` order, and an area module registers its commands the first time it
# is imported. The re-export block below is sorted alphabetically (isort), so it cannot carry that
# order; this loop does, before anything else imports an area — close to the one file's order.
for _area in (
    "setup", "run", "solve", "chat", "serve", "fusion", "skills", "learning", "ops", "work",
    "memory", "evolve", "pet", "misc", "bench",
):
    importlib.import_module(f"chimera.cli.commands.{_area}")

# The `*_cmd` modules predate the split; the area modules mount them, and their names come from the
# modules that define them (an area module's `from x import y` is not an export of its own).
from chimera import __version__  # noqa: E402
from chimera.cli.code_cmd import code_app  # noqa: E402
from chimera.cli.commands._shared import (  # noqa: E402
    _BATCH_TASKS_ARG,
    _CREW_WORKER_OPT,
    _HEADLESS_STDOUT,
    _IMAGE_OPTION,
    _STOP_EXIT_CODES,
    _apply_tool_allowlist,
    _cascade_backend,
    _emit_headless,
    _exit_for,
    _force_utf8_streams,
    _fused_if,
    _headless,
    _headless_payload,
    _json_line,
    _machine_stdout,
    _maybe_defer,
    _print_version,
    _resolve_cli_roles,
    _root,
    _session_profile,
    _set_env_var,
    _stream_sink,
    _task_from_stdin,
    app,
    console,
    owner_identity,
)
from chimera.cli.commands.bench import (  # noqa: E402
    _BENCH_CORPUS,
    _BENCH_K,
    _BENCH_PROBES,
    _BENCH_ROOT,
    _RERANK_K,
    _run_multistep_hierarchy_bench,
    bench,
    bench_app,
    bench_compare,
    bench_rag,
    bench_reranker,
    cascade_bench,
    evoclaw,
    fusion_bench,
    hierarchy_bench,
    measure_app,
    memory_bench,
    memory_poison,
    probe_select,
    redteam,
    sandbox_bench,
    schema_bench,
    skillcard_bench,
    swe_bench_compare,
    transfer_gate,
)
from chimera.cli.commands.chat import (  # noqa: E402
    _ATTACH_HELP,
    _assist_commands,
    _attach_document,
    _chat_commands,
    _check_max_usd,
    _emit_memory_nudges,
    _grounded_answers_for,
    _handle_unknown_command,
    _persist_turn,
    _pinned_notice,
    _print_help,
    _render_memory_note,
    _render_turn,
    _replayed_provenance,
    _resume_or_new,
    _run_task_command,
    _run_turn,
    _sandbox_banner,
    _session_command,
    _switch_model,
    assist,
    chat,
    tui,
)
from chimera.cli.commands.evolve import (  # noqa: E402
    _collector,
    _read_results,
    evolve_app,
    evolve_export,
    evolve_guard,
    evolve_recipe,
    evolve_refine,
    evolve_rft,
    evolve_status,
    evolve_tune,
)
from chimera.cli.commands.fusion import (  # noqa: E402
    brief,
    delegations,
    fuse,
    fusion_receipts,
    maturity,
    orchestrate,
)
from chimera.cli.commands.learning import (  # noqa: E402
    _curation_outcome,
    _lessons_buffer,
    _load_playbook,
    _playbook_path,
    _save_playbook,
    lessons_app,
    lessons_show,
    lessons_vouch,
    migrate,
    playbook_add,
    playbook_app,
    playbook_curate,
    playbook_refine,
    playbook_show,
    playbook_vouch,
    rubric_grade,
)
from chimera.cli.commands.memory import (  # noqa: E402
    _emit_skill_nudges,
    _learned_skill_labels,
    _maybe_autoconsolidate,
    _memory_extractor,
    _memory_manager,
    _recall_graph,
    _record_merge_spend,
    _record_tidy_spend,
    _semantic_embed,
    memory_add,
    memory_app,
    memory_consolidate,
    memory_export,
    memory_graph,
    memory_list,
    memory_profile,
    memory_prune,
    memory_search,
)
from chimera.cli.commands.misc import (  # noqa: E402
    _right_hand_builder,
    _SpendCapReached,
    crew,
    drift,
    find_command,
    lifecycle,
    meta,
    scenarios,
    workflow,
)
from chimera.cli.commands.ops import (  # noqa: E402
    _NOT_APPROVABLE_HERE,
    _ago,
    _cron_store,
    approve,
    cron_add,
    cron_app,
    cron_disable,
    cron_doctor,
    cron_enable,
    cron_fire,
    cron_kill,
    cron_learn,
    cron_list,
    cron_remove,
    report_app,
    report_pr_watch,
    report_weekly,
    secrets_app,
    secrets_list,
    secrets_rm,
    secrets_set,
)
from chimera.cli.commands.pet import (  # noqa: E402
    _pet_interact,
    _pet_store,
    _render_pet,
    pet_app,
    pet_feed,
    pet_new,
    pet_play,
    pet_rest,
    pet_status,
)
from chimera.cli.commands.run import (  # noqa: E402
    _unused_markdown_path,
    _write_binary_deliverable,
    agent,
    deliver,
    guard,
    run,
    sessions,
)
from chimera.cli.commands.serve import (  # noqa: E402
    _bind_app_socket,
    _build_a2a,
    _build_messaging_adapter,
    _chat_approvals,
    _kernel_observes_unless_told_otherwise,
    _messaging_adapter,
    _sender_registry,
    _serve_mcp,
    _serve_platform,
    _start_cron_daemon,
    _turn_attachments,
    _warn_open_bot,
    _webhook_handler,
    _whatsapp_webhook,
    a2a_card,
    acp_server,
    desktop_app,
    resolve_app_workspace,
    serve,
)
from chimera.cli.commands.setup import (  # noqa: E402
    _MODELS_ROLE_ENV,
    _STEPLOG_PATH_OPTION,
    _WIRE_PATH_OPTION,
    _doctor_fixes,
    _render_models,
    agents_app,
    agents_list,
    agents_rm,
    agents_set,
    audit_app,
    audit_reconcile,
    context_curve_cmd,
    doctor,
    features,
    init,
    models_app,
    models_catalog,
    models_main,
    models_set,
    profile_app,
    profile_forget,
    profile_set,
    profile_show,
    version,
)
from chimera.cli.commands.skills import (  # noqa: E402
    _expect_scorer,
    _print_defer_saving,
    _skills_group,
    skills,
    skills_app,
    skills_approve,
    skills_bundle_disable,
    skills_bundle_enable,
    skills_bundles,
    skills_catalog,
    skills_evolve,
    skills_export,
    skills_import,
    skills_install,
    skills_library,
    skills_lifecycle,
    skills_pending,
    skills_retire,
    skills_stats,
    skills_uninstall,
    tools,
)
from chimera.cli.commands.solve import (  # noqa: E402
    _CONVERSATION_GOVERNANCE,
    _SOLVE_COMMAND,
    SolveFailed,
    _append_json_line,
    _last_user_message,
    _report_batch_outcomes,
    _report_collusion,
    _run_solve_command,
    _solve_defaults,
    _solve_from_conversation,
    crew_isolated,
    explore,
    solve,
    solve_batch,
)
from chimera.cli.commands.work import (  # noqa: E402
    _MCP_ARG_OPT,
    _MCP_ENV_OPT,
    _board,
    _mcp_path,
    _print_project,
    _project_lane,
    _resume_project,
    kanban_add,
    kanban_app,
    kanban_board,
    kanban_learn,
    kanban_move,
    kanban_rm,
    kanban_run,
    mcp_add,
    mcp_app,
    mcp_approve,
    mcp_desktop,
    mcp_list,
    mcp_remove,
    mcp_test,
    project_app,
    project_approve,
    project_deny,
    project_run,
    project_start,
    project_status,
    project_step,
)
from chimera.cli.decide_cmd import decide as _decide  # noqa: E402
from chimera.cli.decisions_cmd import decisions_app  # noqa: E402
from chimera.cli.review_cmd import review as _review  # noqa: E402
from chimera.cli.sessions_cmd import sessions_app  # noqa: E402
from chimera.config import config_env_files, get_settings  # noqa: E402
from chimera.governance import governed_profile  # noqa: E402

#: Every name the CLI defined when it was one file, so `from chimera.cli.main import X` still works.
__all__ = [
    "__version__", "config_env_files", "get_settings", "governed_profile", "SolveFailed",
    "_ATTACH_HELP", "_BATCH_TASKS_ARG", "_BENCH_CORPUS", "_BENCH_K", "_BENCH_PROBES",
    "_BENCH_ROOT", "_CONVERSATION_GOVERNANCE", "_CREW_WORKER_OPT", "_HEADLESS_STDOUT",
    "_IMAGE_OPTION", "_MCP_ARG_OPT", "_MCP_ENV_OPT", "_MODELS_ROLE_ENV", "_NOT_APPROVABLE_HERE",
    "_RERANK_K", "_SOLVE_COMMAND", "_STEPLOG_PATH_OPTION", "_STOP_EXIT_CODES", "_SpendCapReached",
    "_WIRE_PATH_OPTION", "_ago", "_append_json_line", "_apply_tool_allowlist", "_assist_commands",
    "_attach_document", "_bind_app_socket", "_board", "_build_a2a", "_build_messaging_adapter",
    "_cascade_backend", "_chat_approvals", "_chat_commands", "_check_max_usd", "_collector",
    "_cron_store", "_curation_outcome", "_decide", "_doctor_fixes", "_emit_headless",
    "_emit_memory_nudges", "_emit_skill_nudges", "_exit_for", "_expect_scorer",
    "_force_utf8_streams", "_fused_if", "_grounded_answers_for", "_handle_unknown_command",
    "_headless", "_headless_payload", "_json_line", "_kernel_observes_unless_told_otherwise",
    "_last_user_message", "_learned_skill_labels", "_lessons_buffer", "_load_playbook",
    "_machine_stdout", "_maybe_autoconsolidate", "_maybe_defer", "_mcp_path", "_memory_extractor",
    "_memory_manager", "_messaging_adapter", "_persist_turn", "_pet_interact", "_pet_store",
    "_pinned_notice", "_playbook_path", "_print_defer_saving", "_print_help", "_print_project",
    "_print_version", "_project_lane", "_read_results", "_recall_graph", "_record_merge_spend",
    "_record_tidy_spend", "_render_memory_note", "_render_models", "_render_pet", "_render_turn",
    "_replayed_provenance", "_report_batch_outcomes", "_report_collusion", "_resolve_cli_roles",
    "_resume_or_new", "_resume_project", "_review", "_right_hand_builder", "_root",
    "_run_multistep_hierarchy_bench", "_run_solve_command", "_run_task_command", "_run_turn",
    "_sandbox_banner", "_save_playbook", "_semantic_embed", "_sender_registry", "_serve_mcp",
    "_serve_platform", "_session_command", "_session_profile", "_set_env_var", "_skills_group",
    "_solve_defaults", "_solve_from_conversation", "_start_cron_daemon", "_stream_sink",
    "_switch_model", "_task_from_stdin", "_turn_attachments", "_unused_markdown_path",
    "_warn_open_bot", "_webhook_handler", "_whatsapp_webhook", "_write_binary_deliverable",
    "a2a_card", "acp_server", "agent", "agents_app", "agents_list", "agents_rm", "agents_set",
    "app", "approve", "assist", "audit_app", "audit_reconcile", "bench", "bench_app",
    "bench_compare", "bench_rag", "bench_reranker", "brief", "cascade_bench", "chat", "code_app",
    "console", "context_curve_cmd", "crew", "crew_isolated", "cron_add", "cron_app",
    "cron_disable", "cron_doctor", "cron_enable", "cron_fire", "cron_kill", "cron_learn",
    "cron_list", "cron_remove", "decisions_app", "delegations", "deliver", "desktop_app", "doctor",
    "drift", "evoclaw", "evolve_app", "evolve_export", "evolve_guard", "evolve_recipe",
    "evolve_refine", "evolve_rft", "evolve_status", "evolve_tune", "explore", "features",
    "find_command", "fuse", "fusion_bench", "fusion_receipts", "guard", "hierarchy_bench", "init",
    "kanban_add", "kanban_app", "kanban_board", "kanban_learn", "kanban_move", "kanban_rm",
    "kanban_run", "lessons_app", "lessons_show", "lessons_vouch", "lifecycle", "maturity",
    "mcp_add", "mcp_app", "mcp_approve", "mcp_desktop", "mcp_list", "mcp_remove", "mcp_test",
    "measure_app", "memory_add", "memory_app", "memory_bench", "memory_consolidate",
    "memory_export", "memory_graph", "memory_list", "memory_poison", "memory_profile",
    "memory_prune", "memory_search", "meta", "migrate", "models_app", "models_catalog",
    "models_main", "models_set", "orchestrate", "owner_identity", "pet_app", "pet_feed", "pet_new",
    "pet_play", "pet_rest", "pet_status", "playbook_add", "playbook_app", "playbook_curate",
    "playbook_refine", "playbook_show", "playbook_vouch", "probe_select", "profile_app",
    "profile_forget", "profile_set", "profile_show", "project_app", "project_approve",
    "project_deny", "project_run", "project_start", "project_status", "project_step", "redteam",
    "report_app", "report_pr_watch", "report_weekly", "resolve_app_workspace", "rubric_grade",
    "run", "sandbox_bench", "scenarios", "schema_bench", "secrets_app", "secrets_list",
    "secrets_rm", "secrets_set", "serve", "sessions", "sessions_app", "skillcard_bench", "skills",
    "skills_app", "skills_approve", "skills_bundle_disable", "skills_bundle_enable",
    "skills_bundles", "skills_catalog", "skills_evolve", "skills_export", "skills_import",
    "skills_install", "skills_library", "skills_lifecycle", "skills_pending", "skills_retire",
    "skills_stats", "skills_uninstall", "solve", "solve_batch", "swe_bench_compare", "tools",
    "transfer_gate", "tui", "version", "workflow",
]


class _ReexportsAreReadOnly(types.ModuleType):
    """This module, refusing to have a re-exported name replaced.

    Before the split, ``chimera.cli.main`` was the one namespace every command read its helpers
    from, so patching a name here changed what the commands saw. Now each command reads the name
    from its own area module, and setting it here would change nothing a command reads — the patch
    "works", the test passes, and it is testing the real helper. Failing loudly turns that silent
    no-op into an error that says where to patch.
    """

    def __setattr__(self, name: str, value: Any) -> None:
        # Re-setting the value it already holds is allowed: it changes nothing, and it is what a
        # patcher's undo does after a refused patch, which must not turn into a second error.
        if name in __all__ and value is not globals().get(name):
            home = getattr(globals().get(name), "__module__", None)
            where = (
                home
                if isinstance(home, str) and home.startswith("chimera.cli.")
                else "the chimera.cli.commands module"
            )
            raise AttributeError(
                f"chimera.cli.main.{name} is a re-export; setting it here reaches no command. "
                f"Patch it where it is looked up: {where}, or whichever area module imports it."
            )
        super().__setattr__(name, value)


sys.modules[__name__].__class__ = _ReexportsAreReadOnly


if __name__ == "__main__":
    app()
