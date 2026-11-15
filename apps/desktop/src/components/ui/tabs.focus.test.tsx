import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { TabPanel } from "@/components/ui/tabs";

/**
 * The panel is a tab stop (`tabIndex=0`), so it must SHOW that it holds focus. It had
 * `focus-visible:outline-hidden`: Tab out of the strip and the focus vanished into the page.
 */
describe("TabPanel focus", () => {
  it("draws the shared focus ring, inside its box, and does not switch the outline off", () => {
    render(
      <TabPanel tabsId="t" value="a">
        content
      </TabPanel>,
    );
    const panel = screen.getByRole("tabpanel");
    expect(panel).toHaveAttribute("tabindex", "0");
    expect(panel.className).toContain("focus-visible:outline-ring");
    expect(panel.className).toContain("focus-visible:outline-solid");
    // Inset: the panel fills a scrolling column, where an outside ring would be clipped.
    expect(panel.className).toContain("focus-visible:-outline-offset-2");
    expect(panel.className).not.toMatch(/outline-(hidden|none)/);
  });
});
