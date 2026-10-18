"""The design system a new document starts with, and the colour arithmetic the checks share.

The defaults ship as ``tokens_default.json`` beside this module (inside the ``chimera`` package, so the
wheel carries it) rather than as literals in code: they are data a person may replace, and the same
file is what a later screen offers as "the default system". Every text/background pair the components
name clears WCAG AA (4.5:1), pinned by a test.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from chimera.design.model import DesignTokens

DEFAULT_TOKENS_PATH = Path(__file__).with_name("tokens_default.json")
#: WCAG 2 AA: normal text needs 4.5:1, large text (24px, or 18.66px bold) needs 3:1.
AA_NORMAL = 4.5
AA_LARGE = 3.0


@lru_cache(maxsize=1)
def _default_text() -> str:
    return DEFAULT_TOKENS_PATH.read_text(encoding="utf-8")


def default_tokens() -> DesignTokens:
    """A fresh copy of the default tokens (callers may mutate what they get)."""
    return DesignTokens.model_validate(json.loads(_default_text()))


def parse_hex(color: str) -> tuple[float, float, float, float]:
    """``#RRGGBB[AA]`` as 0..1 channels. The pattern is validated upstream; this only converts."""
    digits = color.lstrip("#")
    channels = [int(digits[i : i + 2], 16) / 255 for i in range(0, len(digits), 2)]
    alpha = channels[3] if len(channels) == 4 else 1.0
    return channels[0], channels[1], channels[2], alpha


def composite(top: str, bottom: str) -> tuple[float, float, float]:
    """``top`` painted over ``bottom`` (itself painted over white), as opaque RGB."""
    br, bg, bb, ba = parse_hex(bottom)
    base = tuple(c * ba + (1 - ba) for c in (br, bg, bb))
    tr, tg, tb, ta = parse_hex(top)
    return (
        tr * ta + base[0] * (1 - ta),
        tg * ta + base[1] * (1 - ta),
        tb * ta + base[2] * (1 - ta),
    )


def _luminance(rgb: tuple[float, float, float]) -> float:
    def channel(c: float) -> float:
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (channel(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(foreground: str, background: str) -> float:
    """WCAG contrast of ``foreground`` text over ``background`` (alpha composited, over white)."""
    back = composite(background, "#FFFFFF")
    back_hex = "#" + "".join(f"{round(c * 255):02X}" for c in back)
    front = composite(foreground, back_hex)
    lighter, darker = sorted((_luminance(front), _luminance(back)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)
