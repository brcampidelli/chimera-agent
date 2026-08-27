import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { SpendCeiling } from "@/components/code/SpendCeiling";
import { getDoctor } from "@/lib/api";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", () => ({ getDoctor: vi.fn() }));

/**
 * Arming a ceiling, and the two ways one can be armed and still be useless.
 *
 * A spend cap is the only control in this app that can end a turn by itself, so the failures worth
 * testing are the ones where it LOOKS set and is not: a zero, which the server reads as no cap at
 * all, and a default model with no list price, which makes the cap stop the very first call.
 */
function doctor(spend: Record<string, unknown> | null) {
  return {
    has_any_key: true,
    configured_providers: ["openrouter"],
    default_model: "openrouter/deepseek/deepseek-chat",
    tiers: { weak: "w", mid: "m", top: "t" },
    memory_backend: "sqlite",
    cache: true,
    sandbox: "local",
    external_agents: [],
    spend,
  };
}

const PRICED = {
  key: "spend_cap",
  label: "Spend cap (dollar ceiling)",
  available: true,
  probed: true,
  detail: "openrouter/deepseek/deepseek-chat",
  hint: "",
};
const UNPRICED = {
  ...PRICED,
  available: false,
  detail: "vendor/brand-new",
  hint: "no list price known for vendor/brand-new: a spend cap would stop on its first call.",
};

function ceiling(spend: Record<string, unknown> | null = PRICED) {
  const onChange = vi.fn();
  vi.mocked(getDoctor).mockResolvedValue(doctor(spend) as never);
  renderWithProviders(<SpendCeiling onChange={onChange} />);
  return { onChange, field: screen.getByRole("spinbutton") };
}

beforeEach(() => {
  vi.mocked(getDoctor).mockReset();
});

describe("SpendCeiling", () => {
  it("starts armed, and clearing the box disarms it", async () => {
    // It used to start at nothing, which was defensible while a turn could take 8 tool-calling
    // steps. The Code screen now sends 40, and this app is installed by people who did not write
    // it and will not read the settings before their first message.
    //
    // Armed AND visible: a limit nobody can see is a limit nobody can raise, so the number sits in
    // the box rather than in a default somewhere in the backend.
    const { onChange, field } = ceiling();

    expect(Number((field as HTMLInputElement).value)).toBeGreaterThan(0);

    await userEvent.clear(field);

    expect(onChange).toHaveBeenLastCalledWith(null);
  });

  it("arms the ceiling a person types", async () => {
    const { onChange, field } = ceiling();

    await userEvent.clear(field);
    await userEvent.type(field, "0.5");

    expect(onChange).toHaveBeenLastCalledWith(0.5);
  });

  it("refuses to treat a zero as a ceiling, and says so", async () => {
    // The failure this exists for: the server reads `max_usd` for truthiness, so "$0" would mean
    // "spend anything" to the one person certain it meant the opposite. Dropping it silently would
    // leave the box showing a cap that is not there.
    const { onChange, field } = ceiling();

    await userEvent.clear(field);
    await userEvent.type(field, "0");

    expect(onChange).toHaveBeenLastCalledWith(null);
    expect(await screen.findByText(/more than \$0/)).toBeInTheDocument();
  });

  it("warns, with the server's own words, when no ceiling can work on this machine", async () => {
    // `doctor` has measured this since it was written and nothing rendered it. A cap on an unpriced
    // model halts the first call by design — the moment to learn that is before pressing Send, not
    // when the turn stops dead.
    const { field } = ceiling(UNPRICED);

    await userEvent.clear(field);
    await userEvent.type(field, "1");

    expect(await screen.findByText(/no list price known for vendor\/brand-new/)).toBeInTheDocument();
  });

  it("stays quiet when this machine can price its model", async () => {
    const { field } = ceiling(PRICED);

    await userEvent.clear(field);
    await userEvent.type(field, "1");

    await waitFor(() => expect(getDoctor).toHaveBeenCalled());
    expect(screen.queryByText(/cannot work on this machine/)).not.toBeInTheDocument();
  });

  it("says nothing about pricing once the ceiling is cleared again", async () => {
    // Warning about a limiter nobody switched on is noise on every turn of every conversation.
    //
    // Written as arm-then-clear rather than as "render and assert nothing", which is how the first
    // version of this test passed under a deliberately broken gate: `doctor` had not resolved yet,
    // so the warning was absent for the wrong reason and the assertion proved only that the query
    // was slow. Seeing the sentence first is what proves the data landed.
    const { field } = ceiling(UNPRICED);
    await userEvent.clear(field);
    await userEvent.type(field, "1");
    expect(await screen.findByText(/no list price known/)).toBeInTheDocument();

    await userEvent.clear(field);

    await waitFor(() =>
      expect(screen.queryByText(/no list price known/)).not.toBeInTheDocument(),
    );
  });
});
