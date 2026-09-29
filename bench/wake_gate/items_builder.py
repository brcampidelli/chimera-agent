"""Build bench/wake_gate/items.jsonl from the owner's real cron jobs — the registered corpus step.

    .venv/Scripts/python.exe -X utf8 -m bench.wake_gate.items_builder

Reads <home>/scheduler/jobs.json (read-only) and emits one **skeleton row per job** per scenario
kind, with `label` and `why` left empty. Labelling is a human step that happens AFTER this file
exists and BEFORE `run.py` is ever executed: the pre-registration requires the labels committed
before the first call, and this builder cannot make them (a label written by the same code that
writes the state is not a pre-registration — §labels).

The synthetic-event rules are the registration's: ≥10 wake, ≥10 across not_yet/unrelated, ≥5
unrelated, plus the two rule-boundary rows (user event, error event) which the runner asserts and
never scores. The builder writes the skeletons; the human fills label + why, edits where needed,
and commits.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from chimera.config import get_settings  # noqa: E402

OUT = Path(__file__).resolve().parent / "items.jsonl"

EVENT_TIMER = {"kind": "timer"}


def rows_for_job(job: dict) -> list[dict]:
    """One skeleton per scenario family the registration names, per job (the owner's real jobs)."""
    action = (job.get("action") or "")[:600]
    base = {
        "job": {"name": job.get("name", ""), "schedule": job.get("schedule", ""), "action": action},
        "last_result": "",  # filled by hand from the job's last real dispatch, where it matters
        "skipped": 0,
    }
    return [
        {
            "id": f"{job['id']}-timer",
            "label": "",  # TODO(human): wake | not_yet | unrelated — BEFORE any call
            "why": "",  # TODO(human): one sentence naming the thing that changed / the tick that covers it
            **base,
            "event": dict(EVENT_TIMER),
        },
        {
            "id": f"{job['id']}-result-changed",
            "label": "",
            "why": "",
            **base,
            "event": {"kind": "webhook", "name": "last-run-differs"},  # the observed outcome moved
        },
        {
            "id": f"{job['id']}-repeat",
            "label": "",
            "why": "",
            **base,
            "event": {"kind": "webhook", "name": "same-result-again"},  # the tick's answer would not change
        },
    ]


def main() -> None:
    home = get_settings().home
    jobs_path = home / "scheduler" / "jobs.json"
    jobs = json.loads(jobs_path.read_text(encoding="utf-8"))
    rows: list[dict] = []
    for job in jobs:
        if not job.get("enabled", True):
            continue  # a disabled job never dispatches; scenarios about it measure nothing
        rows.extend(rows_for_job(job))
    # The two rule-boundary families, pre-filled: the runner asserts them, never scores them.
    if rows:
        probe = rows[0]
        rows.append({"id": "rule-user-message", "label": "rule:user_event",
                     "why": "asserted: the gate never asks; a user-raised event always wakes",
                     "job": probe["job"], "last_result": "", "skipped": 0,
                     "event": {"kind": "message", "from": "the owner"}})
        rows.append({"id": "rule-error", "label": "rule:error_event",
                     "why": "asserted: the gate never asks; an error or timeout always wakes",
                     "job": probe["job"], "last_result": "", "skipped": 0,
                     "event": {"kind": "webhook", "name": "dispatch-error"}})
    unlabelled = sum(1 for r in rows if not r["label"])
    if OUT.exists():
        raise SystemExit(
            f"{OUT} already exists — a corpus is written once. Edit it by hand; do not regenerate."
        )
    OUT.write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8"
    )
    wake = sum(1 for r in rows if r["label"] == "wake")
    sleep = sum(1 for r in rows if r["label"] in ("not_yet", "unrelated"))
    print(f"wrote {len(rows)} skeleton rows ({unlabelled} unlabelled) to {OUT}")
    print(f"registered minimums: ≥10 wake / ≥10 sleep / ≥5 unrelated — now {wake} wake / {sleep} sleep")
    print("NEXT (human, before any call): fill label + why on every row, using the job's real")
    print("last_result from <home>/scheduler/cron_results.jsonl where the difference matters,")
    print("then commit. run.py refuses to execute until the minimums hold.")


if __name__ == "__main__":
    main()
