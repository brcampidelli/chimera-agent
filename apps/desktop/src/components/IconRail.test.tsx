import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { IconRail, type View } from "@/components/IconRail";
import { I18nProvider } from "@/lib/i18n";
import { TooltipProvider } from "@/components/ui/tooltip";

function renderRail(view: View = "chat") {
  return render(
    <I18nProvider>
      <TooltipProvider>
        <IconRail view={view} onSelect={() => {}} dark onToggleTheme={() => {}} />
      </TooltipProvider>
    </I18nProvider>,
  );
}

describe("IconRail", () => {
  it("carries five destinations, not fifteen", () => {
    renderRail();
    // Fifteen icons stopped being words and became positions to memorise. Five plus Settings is
    // the whole point of the regrouping — if this number creeps back up, so has the problem.
    // (Maturity adds a sixth in development only; this asserts the shipped shape.)
    const nav = screen.getByRole("navigation");
    const destinations = nav.querySelectorAll("button");
    expect(destinations.length).toBeLessThanOrEqual(6);
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
