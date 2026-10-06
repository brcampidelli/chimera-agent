from __future__ import annotations

import httpx

from bench.decision_readout.run import mcnemar_exact, variants
from chimera.decisions import Choice
from chimera.decisions.local import LocalLogprobBackend


def test_default_rendering_prompt_is_byte_for_byte_unchanged() -> None:
    question = Choice("answer", "Choose.", ("ALLOW", "BLOCK"), criteria={"ALLOW": "safe", "BLOCK": "danger"})
    backend = LocalLogprobBackend("http://localhost:11434")
    assert backend.render_mode == "default"
    assert backend.system_text(question) == "Choose.\n- ALLOW: safe\n- BLOCK: danger"
    assert backend.user_suffix(question) == '\n\nAnswer as JSON: {"answer": "ALLOW" | "BLOCK"}'
    assert backend.body("state", question)["messages"] == [
        {"role": "system", "content": "Choose.\n- ALLOW: safe\n- BLOCK: danger"},
        {"role": "user", "content": 'state\n\nAnswer as JSON: {"answer": "ALLOW" | "BLOCK"}'},
    ]


def test_numeric_prefill_and_label_rotation_map_choice_back_to_original() -> None:
    question = Choice("answer", "Choose.", ("ALLOW", "BLOCK"), event=("BLOCK",))
    backend = LocalLogprobBackend("http://localhost:11434", render_mode="numeric", rotation=1)
    body = backend.body("the state", question)
    assert "format" not in body
    assert body["messages"][-1] == {"role": "assistant", "content": "Best answer: ["}
    assert backend.schema(question)["properties"]["answer"]["enum"] == ["1", "2"]
    response = {"message": {"content": "2]"}, "logprobs": [
        {"token": "2", "logprob": -0.1, "top_logprobs": [
            {"token": "1", "logprob": -1.0}, {"token": "2", "logprob": -0.1},
        ]}
    ]}
    reading = backend.read(response, question)
    assert reading.choice == "ALLOW"
    assert reading.shares == {"ALLOW": 0.7109495026250039, "BLOCK": 0.28905049737499605}
    assert reading.p == reading.shares["BLOCK"]


def test_fake_backend_exercises_rendered_request_and_response() -> None:
    calls: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.read() and __import__("json").loads(request.content))
        if request.url.path == "/api/show":
            return httpx.Response(200, json={"details": {"quantization_level": "Q4_K_M"}})
        body = calls[-1]
        assert "Best answer: [" in str(body["messages"][-1]["content"])
        return httpx.Response(200, json={"message": {"content": "1]"}, "logprobs": [
            {"token": "1", "logprob": -0.01, "top_logprobs": [{"token": "1", "logprob": -0.01}, {"token": "2", "logprob": -4.0}]}
        ], "prompt_eval_count": 30})

    question = Choice("answer", "Choose.", ("ALLOW", "BLOCK"))
    client = httpx.Client(transport=httpx.MockTransport(handler))
    backend = LocalLogprobBackend("http://localhost:11434", client=client, render_mode="numeric")
    reading = backend.ask("state", question)
    assert reading.choice == "ALLOW"
    assert reading.resolved_model == "qwen3:4b@Q4_K_M"
    assert [call["model"] for call in calls] == ["qwen3:4b", "qwen3:4b"]


def test_registered_variant_generation_and_exact_mcnemar() -> None:
    question = Choice("answer", "Choose.", ("BLOCK", "REVIEW", "ALLOW"), criteria={"BLOCK": "bad", "REVIEW": "maybe", "ALLOW": "good"})
    assert len(variants(question, "letters_rotation")) == 3
    assert len(variants(question, "label_swap")) == 2
    rows = [
        {"id": "x", "arm": "baseline", "correct": True},
        {"id": "x", "arm": "numeric", "correct": False},
        {"id": "y", "arm": "baseline", "correct": False},
        {"id": "y", "arm": "numeric", "correct": True},
    ]
    assert mcnemar_exact(rows, "numeric") == {
        "baseline_only_correct": 1, "arm_only_correct": 1, "p_two_sided": 1.0
    }


def test_default_request_serialises_to_the_same_bytes_as_before_render_modes() -> None:
    import json

    from chimera.decisions.governance import DANGER

    body = LocalLogprobBackend("http://localhost:11434").body("rm -rf build", DANGER)
    # The key order and content origin/main sent; a reordered dict is a different request body.
    assert list(body) == ["model", "think", "stream", "logprobs", "top_logprobs", "format", "options", "messages"]
    assert json.dumps(body["format"], sort_keys=True) == (
        '{"properties": {"verdict": {"enum": ["BLOCK", "REVIEW", "ALLOW"], "type": "string"}}, '
        '"required": ["verdict"], "type": "object"}'
    )
    assert body["messages"][0]["content"] == DANGER.instructions


def test_label_swap_trades_labels_and_keeps_every_definition_in_place() -> None:
    question = Choice("answer", "Choose.", ("ALLOW", "BLOCK"), criteria={"ALLOW": "safe", "BLOCK": "danger"})
    letters = LocalLogprobBackend("http://localhost:11434", render_mode="letters")
    swap = LocalLogprobBackend("http://localhost:11434", render_mode="swap")
    assert letters.system_text(question) == "Choose.\n- A: safe\n- B: danger"
    assert swap.system_text(question) == "Choose.\n- B: safe\n- A: danger"
    rotated = LocalLogprobBackend("http://localhost:11434", render_mode="letters", rotation=1)
    assert swap.instrument(question) != rotated.instrument(question)
    reading = swap.read({"message": {"content": '{"answer": "A"}'}}, question)
    assert reading.choice == "BLOCK"


def test_numeric_refuses_ten_options_whose_ids_share_a_first_digit() -> None:
    import pytest

    question = Choice("answer", "Choose.", tuple(f"o{i}" for i in range(10)))
    with pytest.raises(ValueError, match="at most 9"):
        LocalLogprobBackend("http://localhost:11434", render_mode="numeric").system_text(question)


def test_every_render_mode_is_a_new_instrument_hash_so_no_map_is_reused() -> None:
    from chimera.decisions.calibration import prompt_hash
    from chimera.decisions.governance import DANGER

    def digest(backend: LocalLogprobBackend) -> str:
        return prompt_hash(backend.name, backend.model, backend.instrument(DANGER))

    base = "http://localhost:11434"
    default = digest(LocalLogprobBackend(base))
    others = {
        digest(LocalLogprobBackend(base, render_mode="numeric")),
        digest(LocalLogprobBackend(base, render_mode="swap")),
        *(digest(LocalLogprobBackend(base, render_mode="letters", rotation=r)) for r in range(3)),
    }
    assert default not in others
    assert len(others) == 5
