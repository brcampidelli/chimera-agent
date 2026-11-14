import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { useId, useState } from "react";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { TabPanel, Tabs } from "@/components/ui/tabs";

/**
 * The tab ↔ panel link, set up the way the screens set it up: one `useId()` handed to both halves,
 * no id typed by hand. `tabs.segmented.test.tsx` passes `"cat"` to each side, which is the one case
 * that cannot show the bug — the defect was two ids that only agreed when somebody wrote them to.
 *
 * Every screen in the app has a `Tabs` and a `TabPanel` that are siblings, not parent and child, so
 * nothing the components can see on their own joins them; the id is the join. The source scan below
 * is what keeps a ninth screen from forgetting it.
 */

function Screen({ paired }: { paired: boolean }) {
  const id = useId();
  const [tab, setTab] = useState<"a" | "b">("a");
  return (
    <>
      <Tabs
        id={paired ? id : undefined}
        aria-label="Sections"
        items={[
          { value: "a", label: "Alpha" },
          { value: "b", label: "Beta" },
        ]}
        value={tab}
        onChange={setTab}
      />
      <TabPanel tabsId={id} value={tab}>
        content
      </TabPanel>
    </>
  );
}

describe("Tabs and TabPanel", () => {
  it("link up when the screen hands both the same useId()", () => {
    render(<Screen paired />);
    const tab = screen.getByRole("tab", { selected: true });
    const controlled = document.getElementById(tab.getAttribute("aria-controls") ?? "");
    expect(controlled, "aria-controls points at nothing").not.toBeNull();
    expect(controlled).toBe(screen.getByRole("tabpanel"));
    // The panel has an accessible name because aria-labelledby resolves to the tab's own text.
    expect(screen.getByRole("tabpanel", { name: "Alpha" })).toBeInTheDocument();
  });

  it("never writes an aria-controls that dangles, whichever tab is selected", () => {
    render(<Screen paired />);
    for (const tab of screen.getAllByRole("tab")) {
      const target = tab.getAttribute("aria-controls");
      if (target !== null) expect(document.getElementById(target), `${target} is not in the page`).not.toBeNull();
    }
  });

  it("leaves aria-controls off a strip that has no panel beside it", () => {
    render(<Screen paired={false} />);
    for (const tab of screen.getAllByRole("tab")) expect(tab).not.toHaveAttribute("aria-controls");
  });
});

function sources(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) return sources(path);
    return /\.tsx$/.test(name) && !/\.test\./.test(name) ? [path] : [];
  });
}

describe("every screen that renders a TabPanel", () => {
  it("passes that same id to its Tabs", () => {
    const broken: string[] = [];
    for (const file of sources(join(__dirname, ".."))) {
      const text = readFileSync(file, "utf8");
      for (const m of text.matchAll(/<TabPanel\s+tabsId=\{(\w+)\}/g)) {
        // The props open a few lines below `<Tabs`; a window is enough to see whether `id={…}` is
        // among them without parsing JSX (the items array has `=>` in it, so "up to the first `>`"
        // would stop early).
        const wired = [...text.matchAll(/<Tabs\b/g)].some((t) =>
          new RegExp(String.raw`\sid=\{${m[1]}\}`).test(text.slice(t.index, t.index + 400)),
        );
        if (!wired) broken.push(`${file.split(/[\\/]/).pop()} (${m[1]})`);
      }
    }
    expect(broken, `TabPanel with no matching <Tabs id>: ${broken.join(", ")}`).toEqual([]);
  });
});
