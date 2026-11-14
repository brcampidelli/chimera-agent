import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { MemorySimilar } from "@/components/MemorySimilar";
import { renderWithProviders } from "@/test/utils";

const getSimilarMemory = vi.fn();
const deleteMemory = vi.fn();

vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  getSimilarMemory: (...a: unknown[]) => getSimilarMemory(...a),
  deleteMemory: (...a: unknown[]) => deleteMemory(...a),
}));

afterEach(() => {
  getSimilarMemory.mockReset();
  deleteMemory.mockReset();
});

const fact = (id: string, content: string, created_at: number) => ({
  id, content, kind: "semantic", provenance: "clean", source: "chat", project: null, created_at,
});

describe("similar facts from different days", () => {
  it("lists one pair with both dates, and resolving removes the chosen fact", async () => {
    getSimilarMemory.mockResolvedValue({
      pairs: [
        {
          a: fact("old", "the release train leaves on thursday", 1_700_000_000),
          b: fact("new", "the release train leaves on friday", 1_700_259_200),
          similarity: 0.75,
        },
      ],
    });
    deleteMemory.mockResolvedValue({ deleted: true });
    renderWithProviders(<MemorySimilar />);

    expect(await screen.findByText("the release train leaves on thursday")).toBeInTheDocument();
    expect(screen.getAllByRole("group")).toHaveLength(1);
    expect(screen.getAllByText(/^written /)).toHaveLength(2);

    const row = screen.getByText("the release train leaves on thursday").closest("div") as HTMLElement;
    await userEvent.click(row.querySelector("button") as HTMLButtonElement);

    await waitFor(() => expect(deleteMemory).toHaveBeenCalledExactlyOnceWith("old"));
  });

  it("draws nothing when there is no pair", async () => {
    getSimilarMemory.mockResolvedValue({ pairs: [] });
    const { container } = renderWithProviders(<MemorySimilar />);

    await waitFor(() => expect(getSimilarMemory).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
  });
});
