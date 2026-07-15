"""Freeze the Chimera backend into the sidecar the Tauri shell bundles + launches.

The self-contained desktop app ships a PyInstaller-frozen copy of the SAME ``chimera app`` server
(FastAPI + uvicorn + the real agent stack + the built SPA) so the installer is zero-dependency: no
system Python, no ``pip install``. This script is the single, cross-platform recipe — run locally on
Windows to smoke it, and re-run verbatim by the release CI on windows/macos/linux.

Usage (from the repo root or anywhere; paths resolve off this file)::

    python apps/desktop/src-tauri/build_sidecar.py            # builds into src-tauri/sidecar-dist/
    python apps/desktop/src-tauri/build_sidecar.py --onefile  # single self-extracting exe (slower boot)

Preconditions: ``pip install pyinstaller`` and the ``[desktop]`` extra, and the SPA built at
``apps/desktop/dist`` (``npm --prefix apps/desktop run build``) — the freeze copies it to
``chimera/_desktop_dist`` so the frozen server serves the UI same-origin.

The onedir output at ``apps/desktop/src-tauri/sidecar-dist/chimera-backend/`` (a ``chimera-backend``
executable + its libs) is what ``tauri.conf.json`` bundles as a resource and ``src/main.rs`` launches
with ``--no-open --port 0 --emit-port-file <tmp>``, then points the window at the URL it writes.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent  # apps/desktop/src-tauri
REPO = HERE.parents[2]  # repo root
SPA_DIST = REPO / "apps" / "desktop" / "dist"
ENTRY = HERE / "sidecar" / "chimera_backend.py"
OUT = HERE / "sidecar-dist"  # gitignored; bundled by Tauri, rebuilt every release


def main() -> int:
    parser = argparse.ArgumentParser(description="Freeze the Chimera backend sidecar.")
    parser.add_argument(
        "--onefile",
        action="store_true",
        help="Single self-extracting exe (simpler to bundle, ~2-5s slower boot). Default: onedir.",
    )
    args = parser.parse_args()

    if not (SPA_DIST / "index.html").exists():
        print(f"error: built SPA not found at {SPA_DIST}", file=sys.stderr)
        print("  build it first: npm --prefix apps/desktop run build", file=sys.stderr)
        return 2

    # --add-data uses os.pathsep between SRC and DEST (';' on Windows, ':' on POSIX) — the one platform
    # wrinkle, handled here so the CI matrix runs this script unchanged on all three OSes.
    add_data = f"{SPA_DIST}{os.pathsep}chimera/_desktop_dist"
    mode = "--onefile" if args.onefile else "--onedir"
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--name", "chimera-backend",
        mode, "--noconfirm", "--clean", "--console",
        "--distpath", str(OUT),
        "--workpath", str(HERE / "sidecar-build"),
        "--specpath", str(HERE / "sidecar-build"),
        # litellm + tiktoken ship data files and dynamic submodules PyInstaller can't infer — collect
        # them whole. chimera's own package data (skills, snapshots) via --collect-data.
        "--collect-all", "litellm",
        "--collect-all", "tiktoken",
        "--collect-all", "tiktoken_ext",
        "--collect-data", "chimera",
        "--add-data", add_data,
        "--hidden-import", "uvicorn",
        "--hidden-import", "sse_starlette",
        str(ENTRY),
    ]
    print("freezing:", " ".join(cmd), flush=True)
    proc = subprocess.run(cmd, check=False)
    if proc.returncode != 0:
        return proc.returncode
    print(f"\nsidecar built under {OUT}", flush=True)
    print("  smoke: run it with --no-open --port 0 --emit-port-file <file>, then curl the URL it writes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
