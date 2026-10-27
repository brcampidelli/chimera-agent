import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { EditDiff } from "@/components/code/EditDiff";
import { LARGE_DIFF_LINES } from "@/components/code/stepSummary";
import { renderWithProviders } from "@/test/utils";

const small = "@@ -1 +1 @@\n-old\n+new";
const large = ["@@ -1,80 +1,80 @@", ...Array.from({ length: LARGE_DIFF_LINES + 5 }, (_, i) => `+row ${i}`)].join("\n");

function mount(patch: string, detailed = false, onOpenFile = vi.fn()) {
  return renderWithProviders(
    <EditDiff path="src/app.py" patch={patch} detailed={detailed} onOpenFile={onOpenFile} />,
  );
}

/** An edit's diff: a small one is open where it lands; a large one folds to "file +N/−M" until asked. */
describe("the diff of an edit", () => {
  it("opens a small diff in place", () => {
    mount(small);
    expect(screen.getByText("+new")).toBeVisible();
    expect(screen.getByText("+1/−1")).toBeInTheDocument();
  });

  it("folds a large diff to its header, showing the counts", () => {
    mount(large);
    expect(screen.getByText("src/app.py").closest("details")).not.toHaveAttribute("open");
    expect(screen.getByText(`+${LARGE_DIFF_LINES + 5}/−0`)).toBeInTheDocument();
  });

  it("opens a large diff when the detailed view is on", () => {
    mount(large, true);
    expect(screen.getByText("src/app.py").closest("details")).toHaveAttribute("open");
  });

  it("opens the file, not the fold, when the path is pressed", async () => {
    const user = userEvent.setup();
    const onOpenFile = vi.fn();
    mount(large, false, onOpenFile);

    await user.click(screen.getByRole("button", { name: "src/app.py" }));
    expect(onOpenFile).toHaveBeenCalledWith("src/app.py");
    expect(screen.getByText("src/app.py").closest("details")).not.toHaveAttribute("open");
  });
});
