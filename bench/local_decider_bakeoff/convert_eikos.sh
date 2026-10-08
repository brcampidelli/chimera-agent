#!/usr/bin/env bash
# Eikos-4B (HF, BF16) -> GGUF Q8_0 with the converter of the same llama.cpp tree as the server (b11457).
# CPU only; text model only (no mmproj: the bake-off sends no images).
set -euo pipefail
SP="C:/Users/brcam/AppData/Local/Temp/claude/C--Users-brcam-Desktop-Desenvolvendo-Projetos-Agent-AI/113de395-42b3-45ce-a32b-def6bb191e16/scratchpad"
PY="$SP/intern/.venv/Scripts/python.exe"
SRC="$SP/s1q/src"
OUT="$SP/bakeoff/weights/eikos-4b-gguf"
mkdir -p "$OUT"
echo "[convert] $(date +%T) llama.cpp $(git -C "$SRC" rev-parse --short HEAD)"
PYTHONPATH="$SRC/gguf-py" PYTHONUTF8=1 "$PY" "$SRC/convert_hf_to_gguf.py" "$SP/bakeoff/weights/eikos-4b" \
  --outtype q8_0 --outfile "$OUT/Eikos-4B-Q8_0.gguf"
ls -la "$OUT"
echo "[done] $(date +%T)"
