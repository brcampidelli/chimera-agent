import { readFileSync } from "node:fs";
import { join } from "node:path";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Screen } from "@/components/ui/panel";

/**
 * A screen rendered inside another screen's tab must not bring a second heading.
 *
 * `Screen` supplies an `<h1>`, a max-width column and its own scroll container unless told it is
 * nested. Usage, Tools and Mcp all take an `embedded` prop — and passed it on exactly ONE of their
 * branches, the error one. So the configuration people actually see, with the data loaded, rendered
 * two headings and two scrollbars, and the branch that got it right was the one where the request
 * had failed.
 *
 * Checked at the source, deliberately. A render test has to arrive at the loaded branch to see the
 * bug, and the first version of this file asserted while the spinner was still up — it passed
 * against a deliberately re-broken main path, which is a test that reports "fine" for the same
 * reason a fixed file does.
 */
const SRC = join(__dirname);

/** The attributes of an opening tag: everything up to the first `>` OUTSIDE a JSX expression.
 *
 *  Brace-aware because `icon={<BarChart3 className="h-5 w-5" />}` carries a `>` of its own, and
 *  stopping at the first one cut every tag short — which made this guard fail on the fixed file. */
function attributes(afterOpen: string): string {
  let depth = 0;
  for (let i = 0; i < afterOpen.length; i += 1) {
    const ch = afterOpen[i];
    if (ch === "{") depth += 1;
    else if (ch === "}") depth -= 1;
    else if (ch === ">" && depth === 0) return afterOpen.slice(0, i);
  }
  return afterOpen;
}

describe("Screen", () => {
  it("renders its own heading when it is the screen", () => {
    render(
      <Screen title="Usage" icon={null}>
        <p>body</p>
      </Screen>,
    );

    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Usage");
  });

  it("renders none when it is inside one", () => {
    render(
      <Screen title="Usage" icon={null} embedded>
        <p>body</p>
      </Screen>,
    );

    expect(screen.queryByRole("heading", { level: 1 })).toBeNull();
    expect(screen.getByText("body")).toBeInTheDocument();
  });
});

describe.each(["Usage", "Tools", "Mcp"])("%s", (name) => {
  it("passes `embedded` to every Screen it renders, not just the error one", () => {
    const source = readFileSync(join(SRC, `${name}.tsx`), "utf8");
    const opens = source.split("<Screen").slice(1);

    expect(opens.length).toBeGreaterThan(1);
    for (const [index, tag] of opens.entries()) {
      expect(attributes(tag), `${name}: <Screen> #${index + 1} drops embedded`).toContain(
        "embedded=",
      );
    }
  });
});
