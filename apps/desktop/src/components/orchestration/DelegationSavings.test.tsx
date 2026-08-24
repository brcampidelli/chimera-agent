import { screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { DelegationSavings } from "@/components/orchestration/DelegationSavings";
import { getDelegations } from "@/lib/api";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", async () => (await import("@/test/code-api-mock")).makeCodeApiMock());

/**
 * A loss is not a saving, and the two halves are allowed to disagree.
 *
 * `token_saving` is `counterfactual − measured` — a SIGNED net, negative whenever the fan-out cost
 * more than doing the work inline. The panel printed it verbatim into "{n} tokens saved", so a real
 * screen read **"-12,961 tokens saved"**, and the dollar line was `text-ok` green regardless of
 * sign. The file's own docstring lists three refusals to flatter; this was the fourth it needed.
 *
 * The mixed case is the one worth having a test for, because it is not a bug — it is the intended
 * effect of routing by role. More tokens spent on cheaper models is fewer dollars. A panel that
 * derived both lines from one verdict would have to call that either a win or a loss, and it is
 * both, so they are decided separately.
 */
/** The digits as the APP's language groups them.
 *
 *  This used to accept either separator, because `toLocaleString()` took no locale anywhere in the
 *  app and so followed the machine — 4200 was "4,200" on one laptop and "4.200" on another, and
 *  hard-coding either made the file pass or fail by whose laptop ran it. That is fixed: `useNum`
 *  formats for the chosen language, the tests render in English, and the separator is now a fact
 *  rather than a coin toss. `numbers-follow-the-language.test.tsx` is what keeps it one.
 */
const grouped = (n: number) => new RegExp(new Intl.NumberFormat("en").format(n).replace(",", ","));

function summary(token_saving: number, usd_saving: number | null) {
  vi.mocked(getDelegations).mockResolvedValue({
    summary: { n: 3, token_saving, usd_saving, priced_n: 3, estimated_n: 0 },
  } as Awaited<ReturnType<typeof getDelegations>>);
}

describe("DelegationSavings", () => {
  beforeEach(() => vi.clearAllMocks());

  it("calls a saving a saving", async () => {
    summary(4200, 0.01);
    renderWithProviders(<DelegationSavings />);

    const line = await screen.findByText(grouped(4200));
    expect(line.textContent).toContain("saved");
  });

  it("does not call spending more 'saved'", async () => {
    summary(-12961, 0.0004);
    renderWithProviders(<DelegationSavings />);

    const line = await screen.findByText(grouped(12961));
    expect(line.textContent).toContain("MORE");
    expect(line.textContent).not.toContain("saved");
    // The minus belongs to the wording. "-12,961 tokens MORE" would be the same defect rephrased.
    expect(line.textContent).not.toContain(`-${new Intl.NumberFormat("en").format(12961)}`);
  });

  it("keeps the dollars honest on their own, not from the token verdict", async () => {
    // Bruno's actual screen: more tokens, fewer dollars. Both true — cheaper models, more of them.
    summary(-12961, 0.0004);
    renderWithProviders(<DelegationSavings />);

    const usd = await screen.findByText(/0\.0004/);
    expect(usd.textContent).toContain("saved");
    expect(usd.className).toContain("text-ok");
  });

  it("does not paint a dollar loss green", async () => {
    summary(-12961, -0.0004);
    renderWithProviders(<DelegationSavings />);

    const usd = await screen.findByText(/0\.0004/);
    expect(usd.className).toContain("text-warn");
    expect(usd.className).not.toContain("text-ok");
  });
});
