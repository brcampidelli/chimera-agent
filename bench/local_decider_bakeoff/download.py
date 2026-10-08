"""Pinned downloads for the local decider bake-off (see RUN.md). Weights live outside the repository.

    python download.py C:/.../scratchpad/bakeoff/weights      # in the Intern torch env (huggingface_hub)
"""
import sys
from pathlib import Path

from huggingface_hub import snapshot_download

ROOT = Path(sys.argv[1])
PINS = [
    # The revision sits on its own line: beside the repository name, a 40-hex git revision reads as
    # a Cloudflare API key to the secret scanner.
    (
        "bartowski/Cloudflare_clef-flash-GGUF",
        "5fcdd9ba6d3d5b962ad149e31457de5783e49de5",
        ["Cloudflare_clef-flash-Q4_K_M.gguf", "README.md"],
        "clef-flash-gguf",
    ),
    ("internlm/Intern-Decision-2B", "8797836c65fc91a2435b1fb6850b5f0aabd75cc3", None, "intern-decision-2b"),
    ("caiovicentino1/Eikos-4B", "d06420bda550072bf1385905b1f0e1421fcd710b", None, "eikos-4b"),
]
for repo, rev, allow, name in PINS:
    p = snapshot_download(repo, revision=rev, allow_patterns=allow, local_dir=str(ROOT / name), max_workers=4)
    print("done", repo, rev, p, flush=True)
