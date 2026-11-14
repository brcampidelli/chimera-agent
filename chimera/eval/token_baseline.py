"""Read-only baseline of tokens and cost, from the logs this machine already keeps (P0-18).

No savings claim in the P1 wave can stand without a "we used to spend this much" measured with the
same ruler. This module is that first reading. It only ever OPENS files for reading: it creates,
moves, rotates and writes nothing under the home, and it never opens ``.env``.

The ruler is the Cost screen's. The rows are ``usage.jsonl`` plus the run receipts rebuilt by
:func:`chimera.api.usage.ledger_rows` (usage joined with the run receipts on the run id), the one
function the dashboard, the weekly review and the daily cap all read, so a run written to both files
is counted once. The pre-registration lives in
``bench/token_baseline/PREREGISTRATION.md`` and this file implements it; where the two disagree the
pre-registration wins and this file is the bug.

What it refuses to do, on purpose:

* guess a surface — a row with no field that names one goes to ``nao_classificada``, counted;
* turn an unknown into a number — a missing price or cache report is left out and counted, never 0;
* print a number for a group smaller than :data:`MIN_N` — it prints the ``n`` and nothing derived;
* print anything that identifies a person or a conversation — only aggregates leave this module.
"""

from __future__ import annotations

import json
import os
from collections import Counter
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from chimera.api.runs import load_runs
from chimera.api.usage import (
    MEMORY_KIND,
    NOT_A_TURN,
    SIDE_ESTIMATED_KIND,
    SIDE_KIND,
    TIDY_KIND,
    UsageRecord,
    ledger_rows,
    summarize_usage,
)

#: Below this many units a group prints only its ``n`` (pre-registered; same for every surface).
MIN_N = 30

#: The three states a metric cell can be in.
INSUFFICIENT = "amostra_insuficiente"
UNAVAILABLE = "nao_disponivel_nesta_fonte"
OK = "ok"

#: Files this reading looks for, relative to the home. ``.1`` is the one rotated generation.
FILES: tuple[str, ...] = (
    "usage.jsonl",
    "runs.jsonl",
    "traces.jsonl",
    "traces.jsonl.1",
    "scheduler/cron_traces.jsonl",
    "scheduler/cron_traces.jsonl.1",
    "code_sessions",
)

#: ``route_kind`` values it is willing to print. ``None`` is the single-model turn. A free-text value
#: outside this closed set is folded into ``outro`` so a field that could hold anything never reaches
#: the output as itself.
_KNOWN_KINDS: frozenset[str] = frozenset(
    {"single", "fusion", "cascade", "hierarchy", MEMORY_KIND, TIDY_KIND, SIDE_KIND, SIDE_ESTIMATED_KIND}
)

#: Surfaces that exist as a row in the report. ``chat`` and ``solve`` are always "not available".
SURFACES: tuple[str, ...] = (
    "code",
    "cron",
    "design",
    "run_autonomo",
    "agents",
    "orquestracao",
    "explore",
    "nao_classificada",
)
_PREFIX_SURFACE: tuple[tuple[str, str], ...] = (
    ("cron:", "cron"),
    ("design:", "design"),
    ("run:", "run_autonomo"),
    ("agents:", "agents"),
    ("orchestration:", "orquestracao"),
    ("explore:", "explore"),
)


# --------------------------------------------------------------------------------------- time ---


def parse_ts(value: object) -> tuple[datetime | None, bool]:
    """``(instant in UTC, was_naive)``. ``None`` when the value does not read as an ISO timestamp.

    A timestamp with no zone is read as UTC and reported as naive so the caller can count it. Dates
    are compared as instants with a zone, never as text.
    """
    if not isinstance(value, str) or not value.strip():
        return None, False
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None, False
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC), True
    return parsed.astimezone(UTC), False


@dataclass
class Diagnostics:
    """What the window filter had to do to the rows; reported so a quiet exclusion is not hidden."""

    ts_sem_fuso: int = 0
    ts_invalido: int = 0
    fora_da_janela: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "ts_sem_fuso": self.ts_sem_fuso,
            "ts_invalido": self.ts_invalido,
            "fora_da_janela": self.fora_da_janela,
        }


def in_window(ts: object, start: datetime, end: datetime, diag: Diagnostics) -> bool:
    """Whether ``ts`` falls in ``[start, end]``, counting the cases that could not be decided."""
    parsed, naive = parse_ts(ts)
    if parsed is None:
        diag.ts_invalido += 1
        return False
    if naive:
        diag.ts_sem_fuso += 1
    if start <= parsed <= end:
        return True
    diag.fora_da_janela += 1
    return False


# --------------------------------------------------------------------------------- home & paths ---


def discover_home(environ: Mapping[str, str], user_home: Path) -> Path | None:
    """The home the desktop app really writes, or ``None``.

    The Tauri shell sets ``CHIMERA_HOME`` to ``<app data dir>/data`` for the backend it starts, and the
    app data dir is the platform's per-user data folder plus the bundle identifier. ``~/.chimera`` is
    the fallback for a plain CLI install and is NOT where the desktop writes. The first candidate that
    holds any of the log files wins.
    """
    candidates: list[Path] = []
    configured = environ.get("CHIMERA_HOME")
    if configured:
        candidates.append(Path(configured).expanduser())
    appdata = environ.get("APPDATA")
    if appdata:
        candidates.append(Path(appdata) / "app.chimera.desktop" / "data")
    candidates.append(user_home / "Library" / "Application Support" / "app.chimera.desktop" / "data")
    candidates.append(user_home / ".local" / "share" / "app.chimera.desktop" / "data")
    candidates.append(user_home / ".chimera")
    for candidate in candidates:
        if any((candidate / name).exists() for name in FILES):
            return candidate
    return None


def sanitize_path(path: Path, environ: Mapping[str, str], user_home: Path) -> str:
    """The home path as it may be shown: no user name, never the full absolute path."""
    appdata = environ.get("APPDATA")
    for root, label in ((Path(appdata) if appdata else None, "%APPDATA%"), (user_home, "~")):
        if root is None:
            continue
        try:
            relative = path.relative_to(root)
        except ValueError:
            continue
        return "/".join([label, *relative.parts])
    return ".../" + path.name


def inventory(home: Path) -> list[dict[str, Any]]:
    """Which of the expected files exist (size only; the content is never described here)."""
    rows: list[dict[str, Any]] = []
    for name in FILES:
        target = home / name
        found = target.exists()
        size = target.stat().st_size if found and target.is_file() else None
        rows.append({"arquivo": name, "encontrado": found, "bytes": size})
    return rows


def code_session_ids(home: Path) -> set[str]:
    """The ids of Code sessions, from the file NAMES in ``code_sessions/`` — no file is opened."""
    folder = home / "code_sessions"
    if not folder.is_dir():
        return set()
    return {entry.name[: -len(".json")] for entry in os.scandir(folder) if entry.name.endswith(".json")}


# ------------------------------------------------------------------------------------ metrics ---


def _int(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def metric(n: int, value: float | int | None, *, digits: int | None = None) -> dict[str, Any]:
    """One metric cell: a number with its ``n``, or the ``n`` alone when the sample is too small."""
    if n < MIN_N or value is None:
        return {"estado": INSUFFICIENT, "n": n}
    return {"estado": OK, "valor": round(value, digits) if digits is not None else value, "n": n}


def _ratio(numerator: float, denominator: float) -> float | None:
    return numerator / denominator if denominator else None


def surface_of(session_id: str, code_ids: set[str]) -> str:
    """Which surface wrote a usage row, from the field that names it; never a guess."""
    for prefix, surface in _PREFIX_SURFACE:
        if session_id.startswith(prefix):
            return surface
    if ":" not in session_id and session_id in code_ids:
        return "code"
    return "nao_classificada"


def kind_of(route_kind: str | None) -> str:
    if route_kind is None or route_kind == "":
        return "single"
    return route_kind if route_kind in _KNOWN_KINDS else "outro"


@dataclass
class UsageAcc:
    """Running sums for one surface's usage rows."""

    rows: int = 0
    turns: int = 0
    prompt: int = 0
    completion: int = 0
    turn_prompt: int = 0
    cache_rows: int = 0
    cache_read: int = 0
    cache_prompt: int = 0
    priced_rows: int = 0
    usd_all: float = 0.0
    turn_priced: int = 0
    turn_usd: float = 0.0
    kinds: Counter[str] = field(default_factory=Counter)

    def add(self, record: UsageRecord) -> None:
        is_turn = record.route_kind not in NOT_A_TURN
        self.rows += 1
        self.prompt += record.prompt_tokens
        self.completion += record.completion_tokens
        self.kinds[kind_of(record.route_kind)] += 1
        if record.cache_read_tokens is not None:
            self.cache_rows += 1
            self.cache_read += record.cache_read_tokens
            self.cache_prompt += record.prompt_tokens
        if record.usd is not None:
            self.priced_rows += 1
            self.usd_all += record.usd
        if is_turn:
            self.turns += 1
            self.turn_prompt += record.prompt_tokens
            if record.usd is not None:
                self.turn_priced += 1
                self.turn_usd += record.usd

    def as_dict(self) -> dict[str, Any]:
        if self.turns < MIN_N:
            # Fewer than MIN_N turns: the n and nothing derived from it (pre-registered).
            return {
                "linhas": self.rows,
                "turnos": {"estado": INSUFFICIENT, "n": self.turns},
                "linhas_auxiliares": self.rows - self.turns,
            }
        out: dict[str, Any] = {
            "linhas": self.rows,
            "turnos": metric(self.turns, self.turns),
            "linhas_auxiliares": self.rows - self.turns,
            "tokens_prompt_por_turno": metric(
                self.turns, _ratio(self.turn_prompt, self.turns), digits=1
            ),
            "taxa_de_cache": metric(
                self.cache_rows, _ratio(self.cache_read, self.cache_prompt), digits=4
            ),
            "cobertura_de_cache": _coverage(self.cache_rows, self.rows),
            "usd_por_turno": metric(self.turn_priced, _ratio(self.turn_usd, self.turn_priced), digits=6),
            "cobertura_de_preco": _coverage(self.priced_rows, self.rows),
            "chamadas_por_papel": _roles(self.kinds, self.rows),
        }
        out["tokens_prompt_total"] = self.prompt
        out["tokens_completion_total"] = self.completion
        out["usd_total_das_linhas_com_preco"] = round(self.usd_all, 6)
        return out


def _coverage(covered: int, total: int) -> dict[str, Any]:
    """How much of the rows a metric could see. Always printed, because it is part of the number."""
    return {"cobertos": covered, "de": total}


def _roles(kinds: Counter[str], rows: int) -> dict[str, Any]:
    """Calls per role, only for the closed set of kinds; ``worker``/``planner``/``juiz`` are not a field."""
    if rows < MIN_N:
        return {"estado": INSUFFICIENT, "n": rows}
    return {
        "estado": OK,
        "n": rows,
        "por_route_kind": dict(sorted(kinds.items())),
        "worker_planner_juiz": UNAVAILABLE,
    }


@dataclass
class TraceAcc:
    """Running sums for the traces attributed to one surface."""

    traces: int = 0
    steps: int = 0
    step_prompt: int = 0
    trunc_seen: int = 0
    truncated: int = 0
    dropped: int = 0

    def add(self, row: Mapping[str, Any]) -> None:
        steps = row.get("steps")
        step_list = [s for s in steps if isinstance(s, dict)] if isinstance(steps, list) else []
        self.traces += 1
        self.steps += len(step_list)
        self.step_prompt += sum(_int(s.get("prompt_tokens")) for s in step_list)
        if "truncated_steps" in row and "dropped_tool_calls" in row:
            self.trunc_seen += 1
            self.truncated += _int(row.get("truncated_steps"))
            self.dropped += _int(row.get("dropped_tool_calls"))

    def as_dict(self) -> dict[str, Any]:
        return {
            "traces": metric(self.traces, self.traces),
            "passos_por_turno": metric(self.traces, _ratio(self.steps, self.traces), digits=2),
            "tokens_prompt_por_passo": metric(
                self.traces, _ratio(self.step_prompt, self.steps), digits=1
            ),
            "truncamentos": _trunc_cell(self),
        }


def _trunc_cell(acc: TraceAcc) -> dict[str, Any]:
    cell = metric(acc.trunc_seen, acc.truncated)
    if cell["estado"] == OK:
        cell["passos_cortados"] = acc.truncated
        cell["chamadas_descartadas"] = acc.dropped
        cell.pop("valor", None)
    cell["traces_sem_o_campo"] = acc.traces - acc.trunc_seen
    return cell


def read_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    """Stream a JSONL file for reading, skipping blank and malformed lines. Never writes."""
    if not path.is_file():
        return
    with path.open(encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict):
                yield row


def _run_ids_of_receipts(home: Path) -> set[str]:
    """Attempt run ids in ``runs.jsonl`` — the join key between a receipt and its trace line."""
    ids: set[str] = set()
    for receipt in load_runs(home / "runs.jsonl"):
        ids.update(a.run_id for a in receipt.attempts if a.run_id)
    return ids


def aggregate_traces(
    home: Path, start: datetime, end: datetime, diag: Diagnostics
) -> tuple[dict[str, TraceAcc], set[str]]:
    """Trace accumulators per surface, plus the run ids of the cron traces (to place cron receipts)."""
    accs = {"cron": TraceAcc(), "run_autonomo": TraceAcc(), "nao_classificada": TraceAcc()}
    receipt_ids = _run_ids_of_receipts(home)
    cron_ids: set[str] = set()
    for name in ("scheduler/cron_traces.jsonl", "scheduler/cron_traces.jsonl.1"):
        for row in read_jsonl(home / name):
            if in_window(row.get("ts"), start, end, diag):
                accs["cron"].add(row)
            rid = row.get("run_id")
            if isinstance(rid, str) and rid:
                cron_ids.add(rid)
    for name in ("traces.jsonl", "traces.jsonl.1"):
        for row in read_jsonl(home / name):
            if not in_window(row.get("ts"), start, end, diag):
                continue
            rid = row.get("run_id")
            target = "run_autonomo" if isinstance(rid, str) and rid in receipt_ids else "nao_classificada"
            accs[target].add(row)
    return accs, cron_ids


@dataclass
class _RunAcc:
    runs: int = 0
    priced: int = 0
    unpriced: int = 0
    usd: float = 0.0


def aggregate_runs(
    home: Path, start: datetime, end: datetime, cron_ids: set[str], diag: Diagnostics
) -> dict[str, Any]:
    """US$ per resolved run, from the receipts, plus the share of cost that was not the worker."""
    resolved = {"cron": _RunAcc(), "run_autonomo": _RunAcc()}
    attempts = overhead_known = 0
    overhead_usd = 0.0
    for receipt in load_runs(home / "runs.jsonl"):
        if not in_window(receipt.ts, start, end, diag):
            continue
        is_cron = any(a.run_id in cron_ids for a in receipt.attempts if a.run_id)
        bucket = "cron" if is_cron else "run_autonomo"
        resolved[bucket].runs += 1
        for attempt in receipt.attempts:
            attempts += 1
            if attempt.overhead_usd is not None:
                overhead_known += 1
                overhead_usd += attempt.overhead_usd
        if not receipt.success:
            continue
        if receipt.usd is None:
            resolved[bucket].unpriced += 1
        else:
            resolved[bucket].priced += 1
            resolved[bucket].usd += receipt.usd
    per_surface: dict[str, Any] = {
        name: {
            "runs": metric(acc.runs, acc.runs),
            "usd_por_run_resolvido": metric(acc.priced, _ratio(acc.usd, acc.priced), digits=6),
            "resolvidos_sem_preco": acc.unpriced,
        }
        for name, acc in resolved.items()
    }
    per_surface["nao_worker"] = {
        "tentativas": attempts,
        "cobertura": _coverage(overhead_known, attempts),
        "usd": metric(overhead_known, overhead_usd, digits=6),
    }
    return per_surface


# ------------------------------------------------------------------------------------- report ---


def _records(home: Path) -> list[UsageRecord]:
    """The Cost screen's rows: usage plus the receipts the usage log does not already hold."""
    return ledger_rows(home)


def build_report(
    home: Path,
    *,
    now: datetime,
    since_days: int = 7,
    environ: Mapping[str, str] | None = None,
    user_home: Path | None = None,
) -> dict[str, Any]:
    """The whole reading as one JSON-able dict of aggregates. Opens files for reading only."""
    env = os.environ if environ is None else environ
    user = Path.home() if user_home is None else user_home
    end = now.astimezone(UTC)
    start = end - timedelta(days=since_days)
    diag_usage, diag_traces, diag_runs = Diagnostics(), Diagnostics(), Diagnostics()
    code_ids = code_session_ids(home)

    window_rows = [r for r in _records(home) if in_window(r.ts, start, end, diag_usage)]
    accs: dict[str, UsageAcc] = {name: UsageAcc() for name in SURFACES}
    total = UsageAcc()
    for record in window_rows:
        accs[surface_of(record.session_id, code_ids)].add(record)
        total.add(record)

    trace_accs, cron_ids = aggregate_traces(home, start, end, diag_traces)
    run_cells = aggregate_runs(home, start, end, cron_ids, diag_runs)

    surfaces: dict[str, Any] = {}
    for name in SURFACES:
        cell = accs[name].as_dict()
        if name in trace_accs:
            cell["por_passo"] = trace_accs[name].as_dict()
        else:
            cell["por_passo"] = UNAVAILABLE
        cell["por_run"] = run_cells.get(name, UNAVAILABLE)
        surfaces[name] = cell
    surfaces["chat"] = {"estado": UNAVAILABLE, "motivo": "sem marca propria em usage.jsonl"}
    surfaces["solve"] = {"estado": UNAVAILABLE, "motivo": "indistinguivel dos demais runs autonomos"}

    panel = summarize_usage(window_rows)
    return {
        "leitura": {
            "janela_inicio_utc": start.isoformat(),
            "janela_fim_utc": end.isoformat(),
            "dias": since_days,
            "home": sanitize_path(home, env, user),
            "arquivos": inventory(home),
            "sessoes_code_conhecidas": len(code_ids),
            "min_n": MIN_N,
        },
        "diagnostico": {
            "usage_e_recibos": diag_usage.as_dict(),
            "traces": diag_traces.as_dict(),
            "runs": diag_runs.as_dict(),
        },
        "total": {**total.as_dict(), "nao_worker": run_cells["nao_worker"]},
        "superficies": surfaces,
        "conferencia": _cross_check(total, panel),
    }


def _cross_check(total: UsageAcc, panel: Mapping[str, Any]) -> dict[str, Any]:
    """The script's totals against ``summarize_usage`` over the same rows (the same ruler)."""
    totals = panel["totals"]
    mine = {
        "turnos": total.turns,
        "tokens_prompt": total.prompt,
        "tokens_completion": total.completion,
    }
    theirs = {
        "turnos": totals["turns"],
        "tokens_prompt": totals["prompt_tokens"],
        "tokens_completion": totals["completion_tokens"],
    }
    usd_matches = abs(total.usd_all - float(totals["usd"])) < 1e-9
    cache_pct = panel.get("cache_hit_pct")
    return {
        "script": mine,
        "painel": theirs,
        "bate": mine == theirs and usd_matches,
        "usd_bate": usd_matches,
        "linhas_sem_preco_painel": totals["unpriced_turns"],
        "cache_painel_read_sobre_prompt": None if cache_pct is None else round(cache_pct, 4),
    }


def render_text(report: Mapping[str, Any]) -> str:
    """A compact human rendering. Aggregates only; the JSON is the full record."""
    lines: list[str] = []
    meta = report["leitura"]
    lines.append(f"janela: {meta['janela_inicio_utc']} ate {meta['janela_fim_utc']} ({meta['dias']} dias)")
    lines.append(f"home: {meta['home']}")
    for entry in meta["arquivos"]:
        lines.append(f"  {'ok ' if entry['encontrado'] else 'nao'} {entry['arquivo']}")
    lines.append(f"diagnostico: {_short(report['diagnostico'])}")
    lines.append("")
    groups = [("TOTAL", report["total"])] + list(report["superficies"].items())
    for name, cell in groups:
        lines.append(f"[{name}]")
        lines.extend(_cell_lines(cell))
    lines.append("")
    lines.append(f"conferencia com o painel: {report['conferencia']}")
    return "\n".join(lines)


def _cell_lines(cell: Mapping[str, Any]) -> list[str]:
    if "estado" in cell:
        return [f"  {cell['estado']}"]
    out: list[str] = []
    for key, value in cell.items():
        out.append(f"  {key}: {_short(value)}")
    return out


def _short(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def now_utc() -> datetime:
    return datetime.now(UTC)
