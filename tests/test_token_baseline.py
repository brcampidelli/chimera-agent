"""The token/cost baseline reads the logs without changing them, leaks nothing, and refuses small samples.

Every file here is synthetic. The privacy test plants a marker in every free-text field a record has
and checks that no output form carries it; the read-only test snapshots the home before and after.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from chimera.api.usage import summarize_usage
from chimera.eval import token_baseline as tb

NOW = datetime(2026, 11, 12, 12, 0, tzinfo=UTC)
# Planted by concatenation so the literal never appears in the source as one string.
SECRET = "sk-" + "PLANTED" + "-" + "0123456789abcdef"


def _ts(hours_ago: float = 1.0) -> str:
    return (NOW - timedelta(hours=hours_ago)).isoformat()


def _write(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row) + "\n")


def _usage(session: str, *, prompt: int = 100, cache: int | None = None, usd: float | None = 0.01,
           kind: str | None = None, ts: str | None = None) -> dict[str, Any]:
    return {
        "ts": ts or _ts(), "session_id": session, "model": "m", "prompt_tokens": prompt,
        "completion_tokens": 10, "cache_read_tokens": cache, "usd": usd, "tools": 0,
        "route_kind": kind,
    }


def _report(home: Path, **kwargs: Any) -> dict[str, Any]:
    return tb.build_report(home, now=NOW, environ={}, user_home=home.parent, **kwargs)


def _usage_part(cell: dict[str, Any]) -> dict[str, Any]:
    """The usage.jsonl-derived keys of a surface cell (the trace and receipt blocks are separate)."""
    return {k: v for k, v in cell.items() if k not in ("por_passo", "por_run")}


def _cron_rows(count: int, **kwargs: Any) -> list[dict[str, Any]]:
    return [_usage(f"cron:job:{i:04d}", **kwargs) for i in range(count)]


def test_a_surface_with_29_turns_prints_only_its_n_and_30_prints_a_number(tmp_path: Path) -> None:
    _write(tmp_path / "usage.jsonl", _cron_rows(29))
    cell = _usage_part(_report(tmp_path)["superficies"]["cron"])
    assert cell == {
        "linhas": 29,
        "turnos": {"estado": tb.INSUFFICIENT, "n": 29},
        "linhas_auxiliares": 0,
    }  # the n and nothing derived: no mean, no sum, no rate

    _write(tmp_path / "usage.jsonl", _cron_rows(30))
    cell = _report(tmp_path)["superficies"]["cron"]
    assert cell["turnos"]["valor"] == 30
    assert cell["tokens_prompt_por_turno"] == {"estado": tb.OK, "valor": 100.0, "n": 30}


def test_a_row_with_no_cache_report_is_out_of_the_denominator_and_into_the_coverage(tmp_path: Path) -> None:
    rows = _cron_rows(30, cache=50, prompt=100) + [
        _usage(f"cron:job:none{i}", cache=None, prompt=1000) for i in range(10)
    ]
    _write(tmp_path / "usage.jsonl", rows)
    cell = _report(tmp_path)["superficies"]["cron"]
    # 30 rows reported 50 of 100; the ten silent rows (1000 prompt each) must not dilute it.
    assert cell["taxa_de_cache"] == {"estado": tb.OK, "valor": 0.5, "n": 30}
    assert cell["cobertura_de_cache"] == {"cobertos": 30, "de": 40}


def test_a_cache_rate_never_prints_for_fewer_than_30_reporting_rows(tmp_path: Path) -> None:
    rows = _cron_rows(5, cache=50) + [_usage(f"cron:job:x{i}", cache=None) for i in range(40)]
    _write(tmp_path / "usage.jsonl", rows)
    cell = _report(tmp_path)["superficies"]["cron"]
    assert cell["taxa_de_cache"] == {"estado": tb.INSUFFICIENT, "n": 5}
    assert cell["cobertura_de_cache"] == {"cobertos": 5, "de": 45}


def test_a_row_with_no_price_is_counted_in_the_coverage_and_never_priced_as_zero(tmp_path: Path) -> None:
    rows = _cron_rows(30, usd=0.02) + [_usage(f"cron:job:free{i}", usd=None) for i in range(10)]
    _write(tmp_path / "usage.jsonl", rows)
    cell = _report(tmp_path)["superficies"]["cron"]
    assert cell["usd_por_turno"] == {"estado": tb.OK, "valor": 0.02, "n": 30}
    assert cell["cobertura_de_preco"] == {"cobertos": 30, "de": 40}


def test_a_run_in_both_usage_and_runs_is_counted_once_and_matches_the_dashboard(tmp_path: Path) -> None:
    both = _usage("cron:job:RUNID1", prompt=500)
    only_runs_attempt = {"run_id": "ONLYRUN", "usd": 0.03, "prompt_tokens": 70, "completion_tokens": 7}
    receipts = [
        {"ts": _ts(), "task": "t", "success": True, "usd": 0.01,
         "attempts": [{"run_id": "RUNID1", "usd": 0.01, "prompt_tokens": 500, "completion_tokens": 10}]},
        {"ts": _ts(), "task": "t", "success": True, "usd": 0.03, "attempts": [only_runs_attempt]},
    ]
    _write(tmp_path / "usage.jsonl", [both])
    _write(tmp_path / "runs.jsonl", receipts)
    report = _report(tmp_path)
    # one row from usage.jsonl + one from the receipt the usage log does NOT already hold.
    assert report["total"]["linhas"] == 2
    assert report["conferencia"]["script"]["turnos"] == 2
    assert report["conferencia"]["bate"] is True
    assert report["conferencia"]["script"] == report["conferencia"]["painel"]


def test_the_totals_match_summarize_usage_over_the_same_rows(tmp_path: Path) -> None:
    rows = _cron_rows(12) + [_usage("memory-row", kind="memory", prompt=7)]
    rows += [_usage(f"plain{i}", ts=_ts(24 * 30)) for i in range(5)]  # outside the window
    _write(tmp_path / "usage.jsonl", rows)
    report = _report(tmp_path)
    in_window = [r for r in map(_as_record, rows) if r.ts >= _ts(24 * 7)]
    panel = summarize_usage(in_window)["totals"]
    assert report["conferencia"]["painel"]["turnos"] == panel["turns"] == 12
    assert report["conferencia"]["script"]["tokens_prompt"] == panel["prompt_tokens"]
    assert report["total"]["linhas_auxiliares"] == 1  # the memory row is not a turn


def _as_record(row: dict[str, Any]) -> Any:
    from chimera.api.usage import UsageRecord

    return UsageRecord.model_validate(row)


def test_surfaces_come_from_the_field_never_from_a_guess(tmp_path: Path) -> None:
    (tmp_path / "code_sessions").mkdir()
    (tmp_path / "code_sessions" / "abc123.json").write_text("{}", encoding="utf-8")
    rows = [
        _usage("abc123"),            # a plain id with a Code session file: Code
        _usage("zzz999"),            # a plain id with none: could be chat — not classified
        _usage("design:propose:x"),
        _usage("run:attempt1"),
        _usage("agents:b1"),
        _usage("orchestration:o1"),
        _usage("explore:e1"),
        _usage("cron:j:r"),
    ]
    _write(tmp_path / "usage.jsonl", rows)
    surfaces = _report(tmp_path)["superficies"]
    for name in ("code", "nao_classificada", "design", "run_autonomo", "agents", "orquestracao",
                 "explore", "cron"):
        assert surfaces[name]["linhas"] == 1, name
    assert surfaces["chat"]["estado"] == tb.UNAVAILABLE
    assert surfaces["solve"]["estado"] == tb.UNAVAILABLE


def test_the_window_compares_instants_with_a_zone_and_counts_what_it_cannot_decide(tmp_path: Path) -> None:
    inside_offset = (NOW - timedelta(hours=2)).astimezone(timezone_plus3()).isoformat()
    rows = [
        _usage("a1", ts=inside_offset),                         # in the window, written at +03:00
        _usage("a2", ts=(NOW - timedelta(hours=1)).replace(tzinfo=None).isoformat()),  # naive: read as UTC
        _usage("a3", ts="not a date"),
        _usage("a4", ts=_ts(24 * 8)),                           # 8 days ago: out
    ]
    _write(tmp_path / "usage.jsonl", rows)
    report = _report(tmp_path)
    assert report["total"]["linhas"] == 2
    diag = report["diagnostico"]["usage_e_recibos"]
    assert diag == {"ts_sem_fuso": 1, "ts_invalido": 1, "fora_da_janela": 1}


def timezone_plus3() -> Any:
    from datetime import timezone

    return timezone(timedelta(hours=3))


def _trace(run_id: str, *, steps: int = 2, prompt: int = 100, truncated: int | None = 0) -> dict[str, Any]:
    row: dict[str, Any] = {
        "run_id": run_id, "ts": _ts(), "task": "t", "stopped_reason": "final",
        "steps": [{"index": i, "prompt_tokens": prompt, "content": "x", "tools": []} for i in range(steps)],
    }
    if truncated is not None:
        row["truncated_steps"] = truncated
        row["dropped_tool_calls"] = 0
    return row


def test_trace_metrics_are_per_surface_and_gated(tmp_path: Path) -> None:
    _write(tmp_path / "scheduler" / "cron_traces.jsonl",
           [_trace(f"c{i}", steps=3, prompt=200, truncated=1) for i in range(30)])
    _write(tmp_path / "traces.jsonl", [_trace(f"u{i}") for i in range(5)])
    surfaces = _report(tmp_path)["superficies"]
    cron = surfaces["cron"]["por_passo"]
    assert cron["passos_por_turno"] == {"estado": tb.OK, "valor": 3.0, "n": 30}
    assert cron["tokens_prompt_por_passo"] == {"estado": tb.OK, "valor": 200.0, "n": 30}
    assert cron["truncamentos"]["passos_cortados"] == 30
    assert surfaces["nao_classificada"]["por_passo"]["passos_por_turno"] == {
        "estado": tb.INSUFFICIENT, "n": 5}
    assert surfaces["code"]["por_passo"] == tb.UNAVAILABLE


def test_a_trace_whose_run_id_is_in_a_receipt_belongs_to_the_autonomous_surface(tmp_path: Path) -> None:
    _write(tmp_path / "runs.jsonl", [
        {"ts": _ts(), "task": "t", "success": True, "usd": 0.5,
         "attempts": [{"run_id": "attached", "usd": 0.5}]}])
    _write(tmp_path / "traces.jsonl", [_trace("attached"), _trace("loose")])
    surfaces = _report(tmp_path)["superficies"]
    assert surfaces["run_autonomo"]["por_passo"]["traces"] == {"estado": tb.INSUFFICIENT, "n": 1}
    assert surfaces["nao_classificada"]["por_passo"]["traces"] == {"estado": tb.INSUFFICIENT, "n": 1}


def test_cost_per_resolved_run_counts_unpriced_resolved_runs_apart(tmp_path: Path) -> None:
    receipts = [
        {"ts": _ts(), "task": "t", "success": True, "usd": 0.10, "attempts": [{"run_id": f"r{i}"}]}
        for i in range(30)
    ]
    receipts.append({"ts": _ts(), "task": "t", "success": True, "usd": None, "attempts": []})
    receipts.append({"ts": _ts(), "task": "t", "success": False, "usd": 9.0, "attempts": []})
    _write(tmp_path / "runs.jsonl", receipts)
    cell = _report(tmp_path)["superficies"]["run_autonomo"]["por_run"]
    assert cell["usd_por_run_resolvido"] == {"estado": tb.OK, "valor": 0.1, "n": 30}
    assert cell["resolvidos_sem_preco"] == 1  # counted, not folded into the mean as 0


def _plant(secret: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    usage = [
        {**_usage(f"cron:{secret}:{i}", kind=secret), "model": secret, "provider": secret, "host": secret}
        for i in range(31)
    ] + [{**_usage(f"{secret}-plain{i}", kind=secret), "model": secret, "provider": secret,
          "host": secret} for i in range(31)]
    receipts = [{
        "ts": _ts(), "task": secret, "answer": secret, "success": True, "usd": 0.1,
        "verify_command": secret, "workspace": secret,
        "attempts": [{"run_id": secret, "model": secret, "provider": secret, "host": secret,
                      "tool_names": [secret], "verify_output": secret, "feedback": secret,
                      "diff_summary": secret, "usd": 0.1, "overhead_usd": 0.01}],
    }]
    traces = [{
        "run_id": secret, "ts": _ts(), "task": secret, "stopped_reason": secret,
        "steps": [{"index": 0, "prompt_tokens": 5, "content": secret, "model": secret,
                   "tools": [{"name": secret, "arguments": secret, "observation": secret}]}],
        "truncated_steps": 0, "dropped_tool_calls": 0,
    } for _ in range(31)]
    return usage, receipts, traces


def test_a_secret_planted_in_every_text_field_appears_in_no_output(tmp_path: Path, capsys: Any) -> None:
    home = tmp_path / "home"
    usage, receipts, traces = _plant(SECRET)
    _write(home / "usage.jsonl", usage)
    _write(home / "runs.jsonl", receipts)
    _write(home / "traces.jsonl", traces)
    _write(home / "scheduler" / "cron_traces.jsonl", traces)
    (home / "code_sessions").mkdir()
    (home / "code_sessions" / f"{SECRET}-plain0.json").write_text(SECRET, encoding="utf-8")
    (home / ".env").write_text(f"OPENROUTER_API_KEY={SECRET}\n", encoding="utf-8")

    report = tb.build_report(home, now=NOW, environ={"APPDATA": str(tmp_path)}, user_home=tmp_path)
    as_json = json.dumps(report, ensure_ascii=False)
    as_text = tb.render_text(report)
    assert SECRET not in as_json and SECRET not in as_text
    assert "PLANTED" not in as_json and "PLANTED" not in as_text
    assert report["superficies"]["code"]["linhas"] == 1  # the id was used internally, never printed
    assert report["total"]["chamadas_por_papel"]["por_route_kind"] == {"outro": 62, "single": 1}  # +1: the receipt attempt

    cli = _load_cli()
    for flags in ([], ["--json"]):
        assert cli.main(["--home", str(home), "--now", NOW.isoformat(), *flags]) == 0
        captured = capsys.readouterr()
        assert SECRET not in captured.out + captured.err
        assert "PLANTED" not in captured.out + captured.err


def _load_cli() -> Any:
    import importlib.util

    path = Path(__file__).resolve().parent.parent / "scripts" / "token_baseline.py"
    spec = importlib.util.spec_from_file_location("token_baseline_cli", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _snapshot(home: Path) -> dict[str, tuple[int, int]]:
    out: dict[str, tuple[int, int]] = {}
    for root, dirs, files in os.walk(home):
        for name in dirs + files:
            full = Path(root) / name
            stat = full.stat()
            out[str(full.relative_to(home))] = (stat.st_mtime_ns, 0 if full.is_dir() else stat.st_size)
    return out


def test_running_the_script_changes_no_file_and_creates_none(tmp_path: Path) -> None:
    usage, receipts, traces = _plant("marker")
    _write(tmp_path / "usage.jsonl", usage)
    _write(tmp_path / "runs.jsonl", receipts)
    _write(tmp_path / "traces.jsonl", traces)
    _write(tmp_path / "scheduler" / "cron_traces.jsonl", traces)
    (tmp_path / "code_sessions").mkdir()
    (tmp_path / "code_sessions" / "a.json").write_text("{}", encoding="utf-8")
    before = _snapshot(tmp_path)

    _report(tmp_path)
    assert _load_cli().main(["--home", str(tmp_path), "--now", NOW.isoformat(), "--json"]) == 0

    assert _snapshot(tmp_path) == before  # same names (nothing created), same mtimes and sizes


def test_the_cli_refuses_a_missing_home_and_a_window_end_without_a_zone(tmp_path: Path) -> None:
    cli = _load_cli()
    assert cli.main(["--home", str(tmp_path / "absent")]) == 2
    assert cli.main(["--home", str(tmp_path), "--now", "2026-11-12T12:00:00"]) == 2


def test_the_home_the_desktop_writes_is_found_before_the_cli_default(tmp_path: Path) -> None:
    desktop = tmp_path / "Roaming" / "app.chimera.desktop" / "data"
    desktop.mkdir(parents=True)
    (desktop / "usage.jsonl").write_text("", encoding="utf-8")
    cli_home = tmp_path / ".chimera"
    cli_home.mkdir()
    (cli_home / "approvals").mkdir()  # the CLI home exists but holds no log: it must not win
    found = tb.discover_home({"APPDATA": str(tmp_path / "Roaming")}, tmp_path)
    assert found == desktop
    assert tb.discover_home({}, tmp_path / "empty") is None


def test_the_printed_home_path_carries_no_user_name(tmp_path: Path) -> None:
    user = tmp_path / "someone"
    path = user / "AppData" / "Roaming" / "app.chimera.desktop" / "data"
    appdata = user / "AppData" / "Roaming"
    assert tb.sanitize_path(path, {"APPDATA": str(appdata)}, user) == "%APPDATA%/app.chimera.desktop/data"
    assert tb.sanitize_path(user / "x" / "h", {}, user) == "~/x/h"
    assert tb.sanitize_path(tmp_path / "elsewhere" / "h", {}, user) == ".../h"


def test_missing_files_are_reported_as_not_found_and_not_estimated(tmp_path: Path) -> None:
    _write(tmp_path / "usage.jsonl", _cron_rows(3))
    meta = _report(tmp_path)["leitura"]
    found = {entry["arquivo"]: entry["encontrado"] for entry in meta["arquivos"]}
    assert found["usage.jsonl"] is True
    assert found["runs.jsonl"] is False and found["traces.jsonl"] is False


def test_auxiliary_rows_do_not_lift_a_surface_over_the_gate(tmp_path: Path) -> None:
    rows = _cron_rows(29) + [_usage(f"cron:job:mem{i}", kind="memory") for i in range(40)]
    _write(tmp_path / "usage.jsonl", rows)
    cell = _usage_part(_report(tmp_path)["superficies"]["cron"])
    assert cell == {
        "linhas": 69,
        "turnos": {"estado": tb.INSUFFICIENT, "n": 29},
        "linhas_auxiliares": 40,
    }
