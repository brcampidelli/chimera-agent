import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { DesignList } from "@/components/design/DesignList";
import { ApiError, createDesign, deleteDesign, listDesigns } from "@/lib/api";
import { sampleDocument } from "@/test/design-fixture";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return { ...real, listDesigns: vi.fn(), createDesign: vi.fn(), deleteDesign: vi.fn() };
});

const ROW = { id: "doc_1", title: "Launch page", updated_at: "2026-10-18T10:00:00Z", revision: 4 };

describe("the list of designs", () => {
  it("says it is loading, then empty, with the one action that fixes it", async () => {
    let resolve: (rows: never[]) => void = () => undefined;
    vi.mocked(listDesigns).mockReturnValue(new Promise((r) => (resolve = r)));
    renderWithProviders(<DesignList onOpen={vi.fn()} />);
    expect(document.querySelector(".animate-spin")).not.toBeNull();
    resolve([]);
    expect(await screen.findByText("No designs yet. Start one with New design.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "New design" })).toBeInTheDocument();
  });

  it("says when it is not allowed, apart from a plain failure with retry", async () => {
    vi.mocked(listDesigns).mockRejectedValueOnce(new ApiError("forbidden", 403));
    const { unmount } = renderWithProviders(<DesignList onOpen={vi.fn()} />);
    expect(await screen.findByText(/not allowed to read your designs/)).toBeInTheDocument();
    unmount();

    vi.mocked(listDesigns).mockRejectedValueOnce(new ApiError("boom", 500));
    renderWithProviders(<DesignList onOpen={vi.fn()} />);
    expect(await screen.findByText("Couldn't load this.")).toBeInTheDocument();
    vi.mocked(listDesigns).mockResolvedValueOnce([ROW]);
    await userEvent.setup().click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByText("Launch page")).toBeInTheDocument();
  });

  it("opens a design, and starts a new one from the conversation's brief", async () => {
    vi.mocked(listDesigns).mockResolvedValue([ROW]);
    vi.mocked(createDesign).mockResolvedValue({ ...sampleDocument(), id: "doc_new" });
    const onOpen = vi.fn();
    const user = userEvent.setup();
    renderWithProviders(<DesignList brief={"A landing page for the café\nwith a menu"} onOpen={onOpen} />);
    await user.click(await screen.findByRole("button", { name: /^Launch page/ }));
    expect(onOpen).toHaveBeenLastCalledWith("doc_1");

    await user.click(screen.getByRole("button", { name: "New design from this brief" }));
    await waitFor(() => expect(createDesign).toHaveBeenCalledWith("A landing page for the café"));
    expect(onOpen).toHaveBeenLastCalledWith("doc_new", "A landing page for the café\nwith a menu");
  });

  it("deletes only after the confirm dialog", async () => {
    vi.mocked(listDesigns).mockResolvedValue([ROW]);
    vi.mocked(deleteDesign).mockResolvedValue({ deleted: true });
    const user = userEvent.setup();
    renderWithProviders(<DesignList onOpen={vi.fn()} />);
    await user.click(await screen.findByRole("button", { name: "Delete Launch page" }));
    const dialog = await screen.findByRole("dialog");
    expect(deleteDesign).not.toHaveBeenCalled();
    await user.click(within(dialog).getByRole("button", { name: "Delete design" }));
    await waitFor(() => expect(deleteDesign).toHaveBeenCalledWith("doc_1"));
  });
});
