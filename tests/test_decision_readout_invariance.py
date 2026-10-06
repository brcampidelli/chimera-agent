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
