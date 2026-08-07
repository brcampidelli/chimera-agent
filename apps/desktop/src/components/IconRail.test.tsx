import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { IconRail, type View } from "@/components/IconRail";
import { I18nProvider } from "@/lib/i18n";
import { TooltipProvider } from "@/components/ui/tooltip";

function renderRail(view: View = "code") {
  return render(
    <I18nProvider>
      <TooltipProvider>
        <IconRail view={view} onSelect={() => {}} dark onToggleTheme={() => {}} />
      </TooltipProvider>
    </I18nProvider>,
  );
}

describe("IconRail", () => {
  it("carries four destinations, not fifteen", () => {
    renderRail();
    // Fifteen icons stopped being words and became positions to memorise. The regrouping cut them
    // to five, and merging the chat into the conversation cut one more — there was never a reason
    // for two doors to the same agent, and the one without the guard was the more permissive door.
    // If this number creeps back up, so has the problem. (Maturity adds one in development only.)
    const nav = screen.getByRole("navigation");
    const destinations = nav.querySelectorAll("button");
    expect(destinations.length).toBeLessThanOrEqual(5);
  });

  it("names every icon-only button", () => {
    renderRail();
    // The rail is entirely icons, so an unnamed button here is invisible to a screen reader.
    for (const button of screen.getAllByRole("button")) {
      expect(button).toHaveAccessibleName();
    }
  });

  it("marks the current destination as the current page", () => {
    renderRail("knowledge");
    expect(screen.getByRole("button", { name: /knowledge/i })).toHaveAttribute(
      "aria-current",
      "page",
    );
  });

  it("does not mark the theme toggle as a page", () => {
    renderRail();
    // It is a control, not a destination — aria-current on it would mean "you are here" about a
    // place that does not exist.
    expect(screen.getByRole("button", { name: /light|dark/i })).not.toHaveAttribute("aria-current");
  });
});
