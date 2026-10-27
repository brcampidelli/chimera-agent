import { screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { Usage } from "@/components/Usage";
import { getConfig, getUsage } from "@/lib/api";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", () => ({
  getUsage: vi.fn(),
  getConfig: vi.fn(),
  patchConfig: vi.fn(),
}));

const TOTALS = { turns: 105, prompt_tokens: 285_000_000, completion_tokens: 0, cache_read_tokens: 0, cache_write_tokens: null, usd: 9.18, unpriced_turns: 0 };

function usage(by_model: Record<string, unknown>[]) {
  return {
    totals: TOTALS,
    by_day: [{ day: "2026-10-20", turns: 105, prompt_tokens: 285_000_000, completion_tokens: 0, usd: 9.18, unpriced: 0 }],
    by_model,
    by_session: [],
    cache_hit_pct: null,
    cache_write_tokens: null,
    route_mix: { single: 105, fusion: 0, cascade: 0 },
  };
}

beforeEach(() => {
  localStorage.setItem("chimera.lang", "en");
  vi.clearAllMocks();
  vi.mocked(getConfig).mockResolvedValue({} as never);
});

/**
 * "By model" printed a dash for the row whose model no run had recorded. A dash reads as a value
 * someone forgot to fill in; the honest label is that the model was not recorded.
 */
describe("the By model panel", () => {
  it("names a row with no recorded model as not recorded, and keeps its numbers", async () => {
    vi.mocked(getUsage).mockResolvedValue(
      usage([
        { model: "", turns: 104, prompt_tokens: 285_000_000, completion_tokens: 0, usd: 9.18, unpriced: 0,
          cache_read_tokens: 0, cache_write_tokens: 0 },
      ]) as never,
    );
    renderWithProviders(<Usage />);

    expect(await screen.findByText("model not recorded")).toBeInTheDocument();
  });

  it("shows a named model as its name, not as unrecorded", async () => {
    vi.mocked(getUsage).mockResolvedValue(
      usage([
        { model: "openrouter/deepseek/deepseek-chat", turns: 1, prompt_tokens: 10, completion_tokens: 5, usd: 0.01,
          unpriced: 0, cache_read_tokens: 0, cache_write_tokens: 0 },
      ]) as never,
    );
    renderWithProviders(<Usage />);

    expect(await screen.findByText("openrouter/deepseek/deepseek-chat")).toBeInTheDocument();
    expect(screen.queryByText("model not recorded")).not.toBeInTheDocument();
  });
});
