import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { StepsSummary } from "@/components/code/StepsSummary";
import type { CodeToolEvent } from "@/lib/api";
import { renderWithProviders } from "@/test/utils";

const tools: CodeToolEvent[] = [
  { name: "write_file", arguments: { path: "site/index.html" }, ok: true, observation: "", duration_seconds: 1.5 },
  { name: "run_tests", arguments: {}, ok: false, observation: "2 failures", duration_seconds: 4 },
];

function mount(props: { detailed?: boolean; onOpenFile?: (path: string) => void } = {}) {
  const onOpenFile = props.onOpenFile ?? vi.fn();
  return {
    onOpenFile,
    ...renderWithProviders(
      <StepsSummary tools={tools} edits={[]} detailed={props.detailed ?? false} onOpenFile={onOpenFile} />,
    ),
  };
}

/**
 * The compact receipt of a finished turn: "N steps in T", the calls that worked, the calls that did
 * not (one click to them), what the turn did, and the full list behind "show all". The detailed view
 * keeps every call open, as it always did.
 */
describe("compact turn receipt", () => {
  it("states the count, the time, the working calls and the failures, with the calls folded", () => {
    mount();

    expect(screen.getByText("2 steps in 5 s")).toBeInTheDocument();
    expect(screen.getByTestId("steps-ok")).toHaveTextContent("1 ok");
    expect(screen.getByTestId("steps-failed")).toHaveTextContent("1 failed · run_tests");
    expect(screen.getByRole("button", { name: "show all" })).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByText("2 failures")).not.toBeInTheDocument();
  });

  it("opens the full list when show all is pressed", async () => {
    const user = userEvent.setup();
    mount();

    await user.click(screen.getByRole("button", { name: "show all" }));
    expect(screen.getByRole("button", { name: "hide steps" })).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText("2 failures")).toBeInTheDocument();
  });

  it("opens an output straight from what happened", async () => {
    const user = userEvent.setup();
    const { onOpenFile } = mount();

    await user.click(screen.getByRole("button", { name: "site/index.html" }));
    expect(onOpenFile).toHaveBeenCalledWith("site/index.html");
  });

  it("the failure chip opens directly on the failed calls, without the ones that worked", async () => {
    const user = userEvent.setup();
    mount();

    await user.click(screen.getByTestId("steps-failed"));
    expect(screen.getByTestId("steps-failed")).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText("2 failures")).toBeInTheDocument();
    expect(screen.queryByText("write_file")).not.toBeInTheDocument();
  });

  it("a second press on the failure chip folds the failed calls away again, with aria-expanded to match", async () => {
    const user = userEvent.setup();
    mount();

    const chip = screen.getByTestId("steps-failed");
    await user.click(chip);
    expect(chip).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText("2 failures")).toBeInTheDocument();

    await user.click(chip);
    expect(chip).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByText("2 failures")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "show all" })).toHaveAttribute("aria-expanded", "false");
  });

  it("the detailed view keeps every call expanded and offers no fold", () => {
    mount({ detailed: true });

    expect(screen.queryByTestId("steps-summary")).not.toBeInTheDocument();
    expect(screen.getByText("2 failures")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "show all" })).not.toBeInTheDocument();
  });
});
