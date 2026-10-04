import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ConversationsCard } from "@/components/ConversationsCard";
import { renderWithProviders as render } from "@/test/utils";

/**
 * Archiving idle conversations (`CHIMERA_ARCHIVE_AFTER_DAYS`), which shipped with the conversation
 * states and no row. The value is written through the same `PATCH /api/config` every other row uses;
 * the server is the one that refuses zero and words, and its refusal is shown under the field.
 */
vi.mock("@/lib/api", () => ({ patchConfig: vi.fn() }));

const api = await import("@/lib/api");

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.patchConfig).mockResolvedValue({ updated: ["CHIMERA_ARCHIVE_AFTER_DAYS"] });
});

describe("archiving idle conversations", () => {
  it("shows the saved number of days, and never as an empty field", () => {
    const { unmount } = render(<ConversationsCard archiveAfterDays={30} />);
    expect(screen.getByLabelText("Archive idle conversations after")).toHaveValue("30");
    unmount();

    render(<ConversationsCard archiveAfterDays={null} />);
    const field = screen.getByLabelText("Archive idle conversations after");
    expect(field).toHaveValue("");
    expect(field).toHaveAttribute("placeholder", "never");
  });

  it("writes the number of days to its own setting", async () => {
    render(<ConversationsCard archiveAfterDays={null} />);

    await userEvent.type(screen.getByLabelText("Archive idle conversations after"), "14");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));

    expect(api.patchConfig).toHaveBeenCalledWith({ CHIMERA_ARCHIVE_AFTER_DAYS: "14" });
  });

  it("clears to never with an empty value", async () => {
    render(<ConversationsCard archiveAfterDays={7} />);

    await userEvent.clear(screen.getByLabelText("Archive idle conversations after"));
    await userEvent.click(screen.getByRole("button", { name: "Save" }));

    expect(api.patchConfig).toHaveBeenCalledWith({ CHIMERA_ARCHIVE_AFTER_DAYS: "" });
  });

  it("shows the server's refusal under the field", async () => {
    vi.mocked(api.patchConfig).mockRejectedValue(
      new Error("CHIMERA_ARCHIVE_AFTER_DAYS must be more than zero; leave it empty to never archive"),
    );
    render(<ConversationsCard archiveAfterDays={null} />);

    await userEvent.type(screen.getByLabelText("Archive idle conversations after"), "0");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("must be more than zero");
  });
});
