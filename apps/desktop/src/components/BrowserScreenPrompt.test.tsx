/** The browser tab's code prompt: it appears when a screen-only action is refused, takes the code once,
 *  and says what happened without ever echoing the code. */

import { act, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { BrowserScreenPrompt } from "@/components/BrowserScreenPrompt";
import { exchangeBrowserScreenCode } from "@/lib/api";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", () => ({
  exchangeBrowserScreenCode: vi.fn(async () => ({ ok: true, expires_at: 1, name: "Windows" })),
}));

function announceScreenNeeded() {
  act(() => {
    window.dispatchEvent(new CustomEvent("chimera:screen-needed"));
  });
}

describe("the browser's code prompt", () => {
  it("stays hidden until a screen-only action is refused", () => {
    renderWithProviders(<BrowserScreenPrompt />);
    expect(screen.queryByText("This needs the owner's screen")).toBeNull();
  });

  it("appears when a screen-only action is refused, and pairs with the typed code", async () => {
    renderWithProviders(<BrowserScreenPrompt />);
    announceScreenNeeded();

    expect(await screen.findByText("This needs the owner's screen")).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText("Pairing code"), "abcd-efgh");
    await userEvent.click(screen.getByRole("button", { name: "Pair this browser" }));

    await waitFor(() => expect(exchangeBrowserScreenCode).toHaveBeenCalledWith("abcd-efgh"));
    expect(await screen.findByText("This browser is paired. Try the action again.")).toBeInTheDocument();
  });

  it("says a wrong or spent code was refused, and clears the field", async () => {
    vi.mocked(exchangeBrowserScreenCode).mockRejectedValueOnce(new Error("refused"));
    renderWithProviders(<BrowserScreenPrompt />);
    announceScreenNeeded();

    await userEvent.type(await screen.findByLabelText("Pairing code"), "ZZZZ-ZZZZ");
    await userEvent.click(screen.getByRole("button", { name: "Pair this browser" }));

    expect(
      await screen.findByText("That code is not right, or it has expired. Ask the desktop app for a new one."),
    ).toBeInTheDocument();
    expect(screen.getByLabelText("Pairing code")).toHaveValue("");
  });
});
