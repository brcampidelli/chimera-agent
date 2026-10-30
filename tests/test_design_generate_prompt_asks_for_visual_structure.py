"""The first-proposal prompt asks for the structure a visible design needs (`chimera.design.prompts`).

0.79.0 produced a landing page that was black text on white in one size: every text element shared
a default size, the artboard background stayed the white default, and placeholders were written as
body text. The renderer draws what the fields say, so the prompt has to ask for those fields. These
tests pin the requirements and check the one example the prompt carries against the real models.
"""

from __future__ import annotations

import json
import re

from chimera.design import prompts
from chimera.design.ops import parse_operations


def _generate() -> str:
    return prompts.GENERATE.format(
        brief="uma landing page", constraints="{}", tokens="{}", assets="[]", document="{}",
        operations=prompts.OPERATIONS_GUIDE,
    )


def test_the_html_prompt_lists_asset_metadata_without_asset_bytes() -> None:
    text = prompts.HTML_GENERATE.format(
        brief="uma landing page", constraints="{}", tokens="{}",
        assets='[{"id": "a_1", "mime": "image/png", "width": 2, "height": 3}]', document="{}",
        operations=prompts.HTML_OPERATIONS_GUIDE,
    )
    assert "Available imported assets (ids, dimensions and MIME only" in text
    assert '"id": "a_1"' in text and "asset bytes are never sent to the model" in text
    assert '<img src="asset:<id>">' in text and "url(asset:<id>)" in text


def test_the_prompt_asks_for_one_typography_token_per_level() -> None:
    text = _generate()

    for level in ("display", "heading", "body", "label"):
        assert level in text
    assert "fontToken on every text element" in text


def test_the_prompt_says_an_artboard_background_is_a_hex_not_a_token_name() -> None:
    text = _generate()

    assert "never a token name" in text
    assert "an artboard left unset is white" in text


def test_the_prompt_asks_for_shapes_for_sections_and_buttons_and_no_placeholder_text() -> None:
    text = _generate()

    assert "rectangle with its fill" in text
    assert "rectangle" in text and "radius" in text
    assert "Never write a placeholder as body text" in text


def test_the_prompt_example_is_made_of_operations_the_models_accept() -> None:
    # The example lines are the only JSON lines in the visual block that start with a node.add.
    examples = [
        json.loads(line)
        for line in _generate().splitlines()
        if re.match(r'^\{"type": "node\.add"', line)
    ]

    assert len(examples) == 3
    parsed = parse_operations(examples)
    assert [op.type for op in parsed] == ["node.add", "node.add", "node.add"]


def test_the_example_is_neutral_so_it_does_not_anchor_the_model_to_one_brand() -> None:
    # The example is a pattern for the operations' shape. A bakery's name or brand colours in it would
    # be copied into every design, whatever the brief says, so it carries neither.
    joined = "\n".join(
        line for line in _generate().splitlines() if re.match(r'^\{"type": "node\.add"', line)
    )

    for brand_word in ("Pão", "Padaria", "Fornalha", "C8102E", "F4E3C1"):
        assert brand_word not in joined
