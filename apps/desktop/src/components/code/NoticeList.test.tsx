import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { NoticeList } from "@/components/code/NoticeList";
import { DICTS, LANGS } from "@/lib/i18n";
import { renderWithProviders } from "@/test/utils";

/**
 * A warning is a line, not a stop and not a card.
 *
 * The turn's limits used to be silent until they were a stop. This line is the third thing, so the
 * cases that matter are the ones where it could quietly go missing: a code this build has never
 * heard of (a newer server), an empty list, and a language that shipped without the words.
 */
describe("the turn's warnings", () => {
  it("says a known warning in the app's own words", () => {
    renderWithProviders(
      <NoticeList items={[{ code: "steps_low", text: "server wording that must not win" }]} />,
    );

    expect(screen.getByText("2 steps left before this turn stops")).toBeInTheDocument();
    expect(screen.queryByText("server wording that must not win")).not.toBeInTheDocument();
  });

  it("names the model whose price is unknown and the amount already spent", () => {
    renderWithProviders(
      <NoticeList
        items={[
          { code: "price_unknown", text: "", data: { model: "vendor/brand-new" } },
          { code: "spend_warn", text: "", data: { usd: 1.0234, warn_usd: 1 } },
        ]}
      />,
    );

    expect(
      screen.getByText("The price of vendor/brand-new is unknown, so this turn's spend is not counted"),
    ).toBeInTheDocument();
    expect(screen.getByText("This turn has spent US$ 1.02 so far")).toBeInTheDocument();
  });

  it("says what the turns running at once spent together, and how many they are", () => {
    renderWithProviders(
      <NoticeList items={[{ code: "combined_spend", text: "", data: { usd: 2.2049, turns: 3 } }]} />,
    );

    expect(
      screen.getByText("The 3 turns running at once have spent US$ 2.20 together"),
    ).toBeInTheDocument();
  });

  it("says how many steps a long turn has taken, and only the latest count", () => {
    renderWithProviders(
      <NoticeList
        items={[{ code: "steps_extended", text: "", data: { steps: 24 } }]}
      />,
    );

    expect(screen.getByText("24 steps done, and it is still working")).toBeInTheDocument();
  });

  it("names the tool that wrote after untrusted input", () => {
    renderWithProviders(
      <NoticeList items={[{ code: "tainted_write", text: "", data: { tool: "edit_file" } }]} />,
    );

    expect(
      screen.getByText("edit_file ran after this turn read untrusted content — check what it wrote"),
    ).toBeInTheDocument();
  });

  it("still tells a warning this build does not know, with the server's words", () => {
    renderWithProviders(<NoticeList items={[{ code: "from_the_future", text: "something new" }]} />);

    expect(screen.getByText("something new")).toBeInTheDocument();
  });

  it("renders nothing when there is nothing to say", () => {
    const { container } = renderWithProviders(<NoticeList items={[]} />);

    expect(container).toBeEmptyDOMElement();
  });

  it("has the known warnings in every language the app offers", () => {
    for (const lang of LANGS) {
      for (const key of [
        "code.notice.stepsLow",
        "code.notice.compacted",
        "code.notice.toolLoopWarn",
        "code.notice.priceUnknown",
        "code.notice.spendWarn",
        "code.notice.stepsExtended",
        "code.notice.taintedWrite",
        "code.notice.combinedSpend",
      ]) {
        expect(DICTS[lang.code][key], `${lang.code} is missing ${key}`).toBeTruthy();
      }
    }
  });
});
