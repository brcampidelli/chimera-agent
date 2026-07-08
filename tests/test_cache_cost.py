"""The caching-aware dollar model reproduces the measured token law and shows how
prompt caching narrows — and can invert — the dollar win."""

from __future__ import annotations

from chimera.eval.cache_cost import CacheModel, compare_under_caching, format_table


def test_token_reduction_matches_the_measured_sweep_law() -> None:
    # Naive token saving = (D-1)/D at Q=D — the exact law the real sweep measured.
    for d, expected in [(2, 0.5), (3, 0.6667), (4, 0.75), (5, 0.8)]:
        assert abs(compare_under_caching(d).token_reduction - expected) < 0.001


def test_caching_narrows_the_dollar_win() -> None:
    # At Q=D=3 the token win is 66.7%, but the cached-dollar win is far smaller.
    c = compare_under_caching(3, model=CacheModel(read_mult=0.1, write_mult=1.0))
    assert c.token_reduction > 0.66
    assert 0.0 < c.dollar_reduction < c.token_reduction
    assert abs(c.dollar_reduction - 0.1667) < 0.001  # 1 - w/(w + r*(D-1))


def test_high_Q_can_invert_the_dollar_win() -> None:
    # As turns grow, the single agent caches its docs while workers re-pay cold —
    # the hierarchy can cost MORE dollars even while it still saves tokens.
    hi = compare_under_caching(3, q=12, model=CacheModel(read_mult=0.1, write_mult=1.0))
    assert abs(hi.token_reduction - 2 / 3) < 0.001  # tokens: still 66.7% (flat in Q)
    assert hi.dollar_reduction < 0.0                # dollars: a loss under aggressive caching


def test_no_caching_makes_dollar_equal_token() -> None:
    # read_mult == write_mult (no cache discount) collapses dollars onto tokens.
    c = compare_under_caching(4, model=CacheModel(read_mult=1.0, write_mult=1.0))
    assert abs(c.dollar_reduction - c.token_reduction) < 0.001


def test_format_table_is_honest_about_being_a_model() -> None:
    text = format_table([compare_under_caching(3)], model=CacheModel())
    assert "a MODEL, not measured" in text


def test_measured_dollar_bridge_prices_cache_at_its_multiplier() -> None:
    from chimera.eval.cache_cost import dollar_cost, measured_dollar_reduction

    # Single agent: docs mostly cache-read after turn 1; scoped: cold input.
    base = dollar_cost(regular_input=1_000, cache_read=9_000, output=200,
                       input_per_m=3.0, output_per_m=15.0)
    scoped = dollar_cost(regular_input=6_000, cache_read=0, output=200,
                         input_per_m=3.0, output_per_m=15.0)
    # 9k cache-read tokens are billed at 0.1x -> baseline input is cheap.
    assert base < scoped  # caching flipped it: the single agent is cheaper in dollars
    assert measured_dollar_reduction(base, scoped) < 0.0


def test_gateway_extracts_cache_tokens_both_shapes() -> None:
    from chimera.providers.gateway import LLMGateway

    class AnthropicUsage:
        cache_read_input_tokens = 800
        cache_creation_input_tokens = 1_200

    read, write = LLMGateway._extract_cache_tokens(AnthropicUsage())
    assert (read, write) == (800, 1_200)

    class OpenAIDetails:
        cached_tokens = 500

    class OpenAIUsage:
        prompt_tokens_details = OpenAIDetails()

    read2, write2 = LLMGateway._extract_cache_tokens(OpenAIUsage())
    assert (read2, write2) == (500, None)

    class NoCache:
        pass

    assert LLMGateway._extract_cache_tokens(NoCache()) == (None, None)
