import { useState } from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { TabPanel, Tabs } from "@/components/ui/tabs";

function Segmented() {
  const [value, setValue] = useState<"mine" | "discover">("mine");
  return (
    <>
      <Tabs
        id="cat"
        variant="segmented"
        aria-label="Show"
        items={[
          { value: "mine", label: "Mine", count: 3 },
          { value: "discover", label: "Discover" },
        ]}
        value={value}
        onChange={setValue}
      />
      <TabPanel tabsId="cat" value={value}>
        {value}
      </TabPanel>
    </>
  );
}

describe("Tabs, segmented", () => {
  it("is still one tab stop with arrow keys, and raises the selected tab instead of underlining it", async () => {
    const user = userEvent.setup();
    render(<Segmented />);
    const mine = screen.getByRole("tab", { name: "Mine3" });
    expect(mine).toHaveClass("bg-card");
    expect(mine.querySelector(".bg-accent-grad")).toBeNull();
    expect(screen.getByRole("tablist")).not.toHaveClass("border-b");

    await user.tab();
    expect(mine).toHaveFocus();
    await user.keyboard("{ArrowRight}");
    const discover = screen.getByRole("tab", { name: "Discover" });
    expect(discover).toHaveFocus();
    expect(discover).toHaveAttribute("aria-selected", "true");
    expect(discover).toHaveAttribute("tabindex", "0");
    expect(mine).toHaveAttribute("tabindex", "-1");
  });

  it("points aria-controls at the panel when given an id", () => {
    render(<Segmented />);
    const tab = screen.getByRole("tab", { name: "Mine3" });
    expect(screen.getByRole("tabpanel")).toHaveAttribute("id", tab.getAttribute("aria-controls"));
  });
});
