"""Grade the bake-off item by item, removing each instance image once its arms are graded.

    python grade_items.py <phase> <grade_dir> <results_dir> [--arms A,D,G,Q] [--keep 1]

Why this exists (2026-09-26): the host disk filled. `run_evaluation --cache_level instance` keeps
every instance image it pulls, and 209 django images plus two benches' checkouts outgrew the drive.
This grades with the official harness's own per-instance function (`run_instance`, the one
`run_evaluation` calls for every prediction), the dataset loaded once, and after the last arm of an
item is graded it removes that item's image by its exact tag. Items are graded in django-version
order, and the previous item's image is removed only after the next one is present (`--keep 1`), so
consecutive items share their lower layers instead of pulling them again.

It writes nothing of its own about verdicts: `run_instance` writes the per-instance `report.json`
under `logs/run_evaluation/<run_id>/<model>/<instance>/`, as `run_evaluation` would, and
`collect_grades.py` reads them. An instance whose report already exists is skipped (resumable).
Only `sweb.eval` images of the items graded here are ever removed; nothing else is touched.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

TIMEOUT_S = 1800  # run_evaluation's default per-instance timeout


def _predictions(res: Path, phase: str, arms: list[str]) -> dict[str, dict[str, dict[str, Any]]]:
    """{instance_id: {arm: prediction}} from the solves, with the harness's own rule for halts."""
    out: dict[str, dict[str, dict[str, Any]]] = {}
    for line in (res / f"{phase}_solves.jsonl").read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        if r["arm"] not in arms or (r.get("halted") and r["halted"] != "timeout"):
            continue
        out.setdefault(r["instance_id"], {})[r["arm"]] = {
            "instance_id": r["instance_id"], "model_name_or_path": f"dflt-{phase}-{r['arm']}",
            "model_patch": r.get("patch") or ""}
    return out


def main() -> None:
    from swebench.harness.run_evaluation import run_instance
    from swebench.harness.test_spec.test_spec import make_test_spec
    from swebench.harness.utils import load_swebench_dataset

    import docker

    ap = argparse.ArgumentParser()
    ap.add_argument("phase")
    ap.add_argument("grade_dir", type=Path)
    ap.add_argument("results_dir", type=Path)
    ap.add_argument("--arms", default="A,D,G,Q")
    ap.add_argument("--keep", type=int, default=1)
    args = ap.parse_args()
    arms = args.arms.split(",")
    os.chdir(args.grade_dir)  # run_instance writes logs/run_evaluation/... relative to the cwd

    preds = _predictions(args.results_dir, args.phase, arms)
    dataset = {i["instance_id"]: i for i in load_swebench_dataset("SWE-bench/SWE-bench_Verified", "test")
               if i["instance_id"] in preds}
    order = sorted(preds, key=lambda iid: (dataset[iid]["version"], iid))
    client = docker.from_env(timeout=600)
    held: list[str] = []  # image tags this run pulled or used and has not removed yet
    graded = skipped = errors = 0
    for n, iid in enumerate(order, 1):
        spec = make_test_spec(dataset[iid], namespace="swebench")
        image = spec.instance_image_key
        used = False
        for arm, pred in sorted(preds[iid].items()):
            run_id = f"dflt_{args.phase}_{arm}"
            report = Path("logs/run_evaluation") / run_id / pred["model_name_or_path"] / iid / "report.json"
            if report.exists() or not pred["model_patch"].strip():
                skipped += 1  # already graded, or empty (never resolved; collect_grades counts it)
                continue
            used = True
            try:
                run_instance(spec, pred, False, False, client, run_id, TIMEOUT_S)
                graded += 1
            except Exception as exc:  # noqa: BLE001 — collect_grades counts a missing report as an error
                errors += 1
                print(f"  {iid} {arm}: {type(exc).__name__}: {exc}"[:300], flush=True)
        if used and image not in held:
            held.append(image)
        while len(held) > args.keep + 1:
            old = held.pop(0)
            try:
                client.images.remove(old, force=False)
            except Exception as exc:  # noqa: BLE001 — an image in use by a leftover container stays
                print(f"  could not remove {old}: {exc}"[:200], flush=True)
        print(f"[{n}/{len(order)}] {iid} graded {graded} skipped {skipped} errors {errors} "
              f"{time.strftime('%H:%M:%S')}", flush=True)
    for old in held:
        try:
            client.images.remove(old, force=False)
        except Exception as exc:  # noqa: BLE001
            print(f"  could not remove {old}: {exc}"[:200], flush=True)
    print(f"done: graded {graded}, skipped {skipped}, errors {errors}", flush=True)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    main()
