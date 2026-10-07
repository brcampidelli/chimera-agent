"""A local sidecar serving Intern-Decision-4B through the vendor's own readout — see PREREGISTRATION.md.

Runs in its OWN Python environment (torch 2.9.1 + transformers 5.14.1), never in Chimera's:

    python server.py --vendor PATH/TO/Intern-Decision --checkpoint PATH/TO/weights [--port 8765]

The vendor code is imported unchanged from a clone pinned at ``VENDOR_COMMIT``. The only thing this
file does differently from the vendor's ``HFBackend.__init__`` is how the model is placed: 9.1 GB of
BF16 weights do not fit an 8 GB laptop GPU, so ``accelerate`` offloads the layers that do not fit to
CPU (``device_map="auto"``). Nothing is quantized. Probabilities are returned raw (T = 1); the
vendor's temperature preset is applied by the report, not here, so both readings come from one run.

``POST /v1/decisions`` takes ``{"state": ..., "questions": {...}}`` — the Decisions API shape — and
returns the vendor's ``predict`` result. ``GET /health`` reports the pins and the device map summary.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

VENDOR_COMMIT = "3572c8a68b5df5dafe02d0e093989ba8ec0183bc"
PRESET_KEY = "intern-decision-4b"


def verify_vendor(vendor: Path) -> None:
    head = subprocess.run(
        ["git", "-C", str(vendor), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()
    if head != VENDOR_COMMIT:
        raise SystemExit(f"vendor clone at {head}, the pre-registration pins {VENDOR_COMMIT}")


def verify_checkpoint(vendor: Path, checkpoint: Path) -> dict[str, Any]:
    """Every file the vendor's 4B temperature preset hashes must match, byte for byte."""
    presets = json.loads(
        (vendor / "benchmarks" / "temperature-presets.json").read_text(encoding="utf-8")
    )
    preset = presets["models"][PRESET_KEY]
    for name, expected in preset["checkpoint_hashes"].items():
        digest = hashlib.sha256()
        with (checkpoint / name).open("rb") as fh:
            for chunk in iter(lambda: fh.read(8 << 20), b""):
                digest.update(chunk)
        if digest.hexdigest() != expected:
            raise SystemExit(f"checkpoint file {name} does not match the vendor preset hash")
    return {
        "temperature": float(preset["temperature"]),
        "files_checked": len(preset["checkpoint_hashes"]),
        "preset_backend": preset.get("inference_backend"),
    }


def build_engine(vendor: Path, checkpoint: Path, gpu_gib: float) -> Any:
    sys.path.insert(0, str(vendor))
    import torch
    from src.inference import hf_backend
    from src.inference.engine import DecisionEngine
    from transformers import AutoProcessor, AutoTokenizer, Qwen3_5ForConditionalGeneration

    backend = object.__new__(hf_backend.HFBackend)
    # Mirrors HFBackend.__init__ line by line, except the model placement.
    backend.checkpoint = str(checkpoint.resolve())
    backend.media_root = Path(".").resolve()
    backend.max_length = 8192
    backend.device = torch.device("cuda")
    backend.tokenizer = AutoTokenizer.from_pretrained(str(checkpoint), local_files_only=True)
    if hf_backend.DECISION_TOKEN not in backend.tokenizer.get_added_vocab():
        raise SystemExit("checkpoint lacks the trained decision token")
    backend.marker_id = backend.tokenizer.convert_tokens_to_ids(hf_backend.DECISION_TOKEN)
    backend.processor = AutoProcessor.from_pretrained(str(checkpoint), local_files_only=True)
    backend.processor.tokenizer = backend.tokenizer
    backend.model = Qwen3_5ForConditionalGeneration.from_pretrained(
        str(checkpoint),
        dtype=torch.bfloat16,
        local_files_only=True,
        attn_implementation="sdpa",
        device_map="auto",
        max_memory={0: f"{gpu_gib}GiB", "cpu": "48GiB"},
    ).eval()
    if backend.marker_id >= backend.model.get_input_embeddings().weight.shape[0]:
        raise SystemExit("decision marker exceeds the checkpoint vocabulary")

    engine = object.__new__(DecisionEngine)
    engine.checkpoint, engine.temperature, engine.calibration_path = backend.checkpoint, 1.0, None
    engine.backend_name, engine.backend, engine.tokenizer = "hf", backend, backend.tokenizer
    return engine


def device_summary(engine: Any) -> dict[str, int]:
    placed: dict[str, int] = {}
    for device in (getattr(engine.backend.model, "hf_device_map", None) or {}).values():
        placed[str(device)] = placed.get(str(device), 0) + 1
    return placed


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--vendor", type=Path, required=True)
    ap.add_argument("--checkpoint", type=Path, required=True)
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--gpu-gib", type=float, default=6.0)
    args = ap.parse_args()

    verify_vendor(args.vendor)
    pins = verify_checkpoint(args.vendor, args.checkpoint)
    print(f"checkpoint verified: {pins}", flush=True)
    t0 = time.perf_counter()
    engine = build_engine(args.vendor, args.checkpoint, args.gpu_gib)
    placed = device_summary(engine)
    print(f"model loaded in {time.perf_counter() - t0:.0f}s · device map {placed}", flush=True)

    import uvicorn
    from fastapi import FastAPI, HTTPException
    from src.inference.pipeline import validate_request

    app = FastAPI()

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {"vendor_commit": VENDOR_COMMIT, "device_map": placed, **pins}

    @app.post("/v1/decisions")
    def decide(request: dict[str, Any]) -> dict[str, Any]:
        try:
            row = validate_request(request)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        t = time.perf_counter()
        result = engine.predict(row)
        result["timing"]["server_ms"] = round((time.perf_counter() - t) * 1000, 2)
        result["model"] = "internlm/Intern-Decision-4B"
        return result

    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
