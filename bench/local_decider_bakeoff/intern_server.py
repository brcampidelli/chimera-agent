"""Intern-Decision-2B through the existing Intern sidecar — see PREREGISTRATION.md (written first).

Runs in the Intern torch environment, like ``bench/intern_decision_local/server.py``, whose functions
it reuses unchanged: vendor clone pinned at ``VENDOR_COMMIT``, the checkpoint verified against the
vendor's ``temperature-presets.json`` — here the ``intern-decision-2b`` entry — and the vendor's own
``predict`` at T = 1 (the 2B preset is applied by the report, for calibration only).

    python intern_server.py --vendor PATH/TO/Intern-Decision --checkpoint PATH/TO/Intern-Decision-2B [--port 8765]

The 2B BF16 weights (4.4 GB) fit the 8 GB GPU, so ``--gpu-gib`` defaults high enough that nothing
is offloaded; the health route reports the device map so a partial offload cannot pass unseen.
"""

from __future__ import annotations

import argparse
import importlib.util
import time
from pathlib import Path
from typing import Any

PRESET_KEY = "intern-decision-2b"
MODEL = "internlm/Intern-Decision-2B@8797836"


def _sidecar() -> Any:
    path = Path(__file__).resolve().parents[1] / "intern_decision_local" / "server.py"
    spec = importlib.util.spec_from_file_location("intern_sidecar", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.PRESET_KEY = PRESET_KEY  # the only change: the 2B entry of the vendor's presets
    return module


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--vendor", type=Path, required=True)
    ap.add_argument("--checkpoint", type=Path, required=True)
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--gpu-gib", type=float, default=7.5)
    args = ap.parse_args()

    sidecar = _sidecar()
    sidecar.verify_vendor(args.vendor)
    pins = sidecar.verify_checkpoint(args.vendor, args.checkpoint)
    print(f"checkpoint verified: {pins}", flush=True)
    t0 = time.perf_counter()
    engine = sidecar.build_engine(args.vendor, args.checkpoint, args.gpu_gib)
    placed = sidecar.device_summary(engine)
    print(f"model loaded in {time.perf_counter() - t0:.0f}s · device map {placed}", flush=True)

    import uvicorn
    from fastapi import FastAPI, HTTPException
    from src.inference.pipeline import validate_request

    app = FastAPI()

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {"vendor_commit": sidecar.VENDOR_COMMIT, "device_map": placed, "model": MODEL, **pins}

    @app.post("/v1/decisions")
    def decide(request: dict[str, Any]) -> dict[str, Any]:
        request = {k: v for k, v in request.items() if k != "model"}
        try:
            row = validate_request(request)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        t = time.perf_counter()
        result: dict[str, Any] = engine.predict(row)
        result["timing"]["server_ms"] = round((time.perf_counter() - t) * 1000, 2)
        result["model"] = MODEL
        return result

    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
