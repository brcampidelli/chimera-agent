"""Build a run report from the official harness's per-instance reports.

    python collect_grades.py <grade_dir> <phase> <arm> <out_dir>

`run_evaluation` writes one `report.json` per instance, then aggregates them into
`<model>.<run_id>.json`. Under this machine's load the aggregation step itself failed: it lists the
Docker images first, and the daemon's listing timed out (60 s) after every instance had been graded.
The verdicts were on disk. This reads them in the same shape the aggregate has
(`resolved_ids`, `unresolved_ids`, `error_ids`, `empty_patch_ids`, ...), and the analysis reads
either. No verdict is computed here; an instance with a patch and no per-instance report is an error.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def collect(grade_dir: Path, phase: str, arm: str) -> dict[str, object]:
    model, run_id = f"dflt-{phase}-{arm}", f"dflt_{phase}_{arm}"
    preds = [json.loads(ln) for ln in (grade_dir / f"predictions_{phase}_{arm}.jsonl")
             .read_text(encoding="utf-8").splitlines() if ln.strip()]
    logs = grade_dir / "logs" / "run_evaluation" / run_id / model
    resolved, unresolved, errors, empty = [], [], [], []
    for p in preds:
        iid = p["instance_id"]
        if not (p.get("model_patch") or "").strip():
            empty.append(iid)
            continue
        rep = logs / iid / "report.json"
        if not rep.exists():
            errors.append(iid)
            continue
        verdict = json.loads(rep.read_text(encoding="utf-8")).get(iid, {})
        (resolved if verdict.get("resolved") else unresolved).append(iid)
    completed = sorted(resolved + unresolved)
    return {
        "source": "per-instance report.json files of the official harness (collect_grades.py)",
        "submitted_instances": len(preds), "completed_instances": len(completed),
        "resolved_instances": len(resolved), "unresolved_instances": len(unresolved),
        "empty_patch_instances": len(empty), "error_instances": len(errors),
        "submitted_ids": sorted(p["instance_id"] for p in preds), "completed_ids": completed,
        "resolved_ids": sorted(resolved), "unresolved_ids": sorted(unresolved),
        "empty_patch_ids": sorted(empty), "error_ids": sorted(errors),
    }


if __name__ == "__main__":
    grade_dir, phase, arm, out_dir = Path(sys.argv[1]), sys.argv[2], sys.argv[3], Path(sys.argv[4])
    rep = collect(grade_dir, phase, arm)
    out = out_dir / f"dflt-{phase}-{arm}.dflt_{phase}_{arm}.json"
    out.write_text(json.dumps(rep, indent=2) + "\n", encoding="utf-8", newline="\n")
    # Counts of verdicts are not printed while the run is in progress: nothing reads them before the
    # main run ends.
    print(f"{out.name}: submitted {rep['submitted_instances']} completed {rep['completed_instances']} "
          f"empty {rep['empty_patch_instances']} errors {rep['error_instances']}")
