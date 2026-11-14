import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { TurnReceipt } from "@/components/code/Conversation";
import type { CodeTurnDone } from "@/lib/api";
import { renderWithProviders } from "@/test/utils";

const deleteMemory = vi.fn();

vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  deleteMemory: (...a: unknown[]) => deleteMemory(...a),
}));

afterEach(() => deleteMemory.mockReset());

/**
 * The receipt used to say how many facts a turn used and nothing about which. A wrong or stale fact
 * steering an answer could be neither found nor removed from where it was noticed.
 */
const BASE: CodeTurnDone = {
  answer: "ok",
  steps: 1,
  stopped_reason: "final",
  tool_names: [],
  model: "m",
  prompt_tokens: 10,
  completion_tokens: 5,
  usd: 0.001,
  route_meta: null,
  context_peak_tokens: 0,
  memory_facts_used: 3,
  memory_layer: "keyword",
  memory_facts: [
    { id: "a", content: "the staging db is on port 5433", source: "chat", kind: "semantic", unverified: false },
    { id: "b", content: "the db password rotates monthly", source: "extracted", kind: "semantic", unverified: true },
    { id: "c", content: "backups run at night", source: "chimera", kind: "semantic", unverified: false },
  ],
};

function receipt(extra: Partial<CodeTurnDone> = {}) {
  return renderWithProviders(<TurnReceipt done={{ ...BASE, ...extra }} t={(k) => k} />);
}

describe("the facts a turn used", () => {
  it("lists the three facts, each with where it came from", () => {
    receipt();

    const rows = screen.getAllByRole("listitem");
    expect(rows).toHaveLength(3);
    expect(screen.getByText("the staging db is on port 5433")).toBeInTheDocument();
    // Source labels go through the same translation the Memory screen uses.
    expect(screen.getAllByText("code.chat.recalledFrom")).toHaveLength(3);
  });

  it("marks only the fact learned from untrusted content", () => {
    receipt();

    expect(screen.getAllByText("memory.unverified")).toHaveLength(1);
    const unverifiedRow = screen.getByText("the db password rotates monthly").closest("li");
    expect(unverifiedRow).toHaveTextContent("memory.unverified");
  });

  it("forgetting calls the delete route with that fact's id and drops the row", async () => {
    deleteMemory.mockResolvedValue({ deleted: true });
    receipt();

    const row = screen.getByText("the db password rotates monthly").closest("li") as HTMLElement;
    await userEvent.click(row.querySelector("button") as HTMLButtonElement);

    expect(deleteMemory).toHaveBeenCalledExactlyOnceWith("b");
    await waitFor(() => expect(screen.queryByText("the db password rotates monthly")).not.toBeInTheDocument());
    expect(screen.getAllByRole("listitem")).toHaveLength(2);
  });

  it("keeps the row, and says so, when the route fails", async () => {
    deleteMemory.mockRejectedValue(new Error("boom"));
    receipt();

    const row = screen.getByText("backups run at night").closest("li") as HTMLElement;
    await userEvent.click(row.querySelector("button") as HTMLButtonElement);

    expect(await screen.findByRole("alert")).toHaveTextContent("code.chat.recalledForgetFailed");
    expect(screen.getByText("backups run at night")).toBeInTheDocument();
  });

  it("falls back to the plain chip for a frame that only counted the facts", () => {
    receipt({ memory_facts: undefined });

    expect(screen.queryByRole("listitem")).not.toBeInTheDocument();
    expect(screen.getByText("activity.memoryRecall.many")).toBeInTheDocument();
  });
});
