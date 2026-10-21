#!/usr/bin/env python3
"""Generate the Chrome Web Store icon sizes from the desktop's canonical icon."""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "apps/desktop/src-tauri/icons/icon.png"
DESTINATION = ROOT / "extensions/chrome/icons"
SIZES = (16, 32, 48, 128)


def generate_icons() -> None:
    DESTINATION.mkdir(parents=True, exist_ok=True)
    with Image.open(SOURCE) as source:
        image = source.convert("RGBA")
        for size in SIZES:
            image.resize((size, size), Image.Resampling.LANCZOS).save(
                DESTINATION / f"icon{size}.png", format="PNG", optimize=False
            )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    generate_icons()


if __name__ == "__main__":
    main()
