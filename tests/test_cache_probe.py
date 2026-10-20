from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

from scripts import cache_probe
from scripts.cache_probe import MODELS, cache_read_tokens, main


def test_cache_read_parser_preserves_unknown_and_zero() -> None:
    assert cache_read_tokens(SimpleNamespace()) is None
    assert cache_read_tokens({"prompt_tokens_details": {"cached_tokens": 0}}) == 0
    assert cache_read_tokens({"cache_read_input_tokens": 17}) == 17
    assert cache_read_tokens(SimpleNamespace(prompt_tokens_details=SimpleNamespace(cached_tokens=21))) == 21


def test_dry_run_is_opt_in_and_names_three_routes(monkeypatch: Any, capsys: Any) -> None:
    monkeypatch.setattr("sys.argv", ["cache_probe.py"])
    assert main() == 0
    output = capsys.readouterr().out
    assert '"will_send_requests": false' in output
    assert '"calls": 6' in output
    assert set(MODELS) == {"luna", "glm", "deepseek"}

def test_run_probe_writes_two_observations_using_the_injected_provider(
    tmp_path: Any, monkeypatch: Any, capsys: Any
) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    calls: list[tuple[str, list[dict[str, str]], dict[str, Any]]] = []

    def fake_completion(**kwargs: Any) -> Any:
        calls.append((kwargs["model"], kwargs["messages"], kwargs))
        return {"usage": {"prompt_tokens": 80, "prompt_tokens_details": {"cached_tokens": len(calls) * 7}}}

    output = tmp_path / "probe.jsonl"
    cache_probe.run_probe(
        output=output,
        models={"fake": "provider/model"},
        provider_order="route-a",
        completion=fake_completion,
    )

    rows = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
    assert len(calls) == len(rows) == 2
    assert [row["repetition"] for row in rows] == [1, 2]
    assert [row["cache_read_tokens"] for row in rows] == [7, 14]
    assert all(call[0] == "provider/model" for call in calls)
    assert calls[0][1] == calls[1][1]
    assert calls[0][2]["extra_body"] == {"provider": {"order": ["route-a"], "allow_fallbacks": False}}
    assert capsys.readouterr().out.count('"cache_read_tokens"') == 2


def test_live_mode_refuses_missing_key_before_import_or_network(monkeypatch: Any, capsys: Any) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setattr("sys.argv", ["cache_probe.py", "--run"])
    try:
        main()
    except SystemExit as exc:
        assert "required" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("live mode must fail closed without credentials")
    assert capsys.readouterr().out == ""
