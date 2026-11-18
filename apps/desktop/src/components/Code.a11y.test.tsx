import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { Code } from "@/components/Code";
import { AgentProvider, useAgent } from "@/lib/agent-context";
import {
  getFsTree,
  getGitStatus,
  getPostureFacts,
  getRuns,
  streamCodeTurn,
  type CodeApprovalEvent,
} from "@/lib/api";
import { emptyTree, gitStatus, postureFacts, scriptTurn } from "@/test/code-api-mock";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", async () => (await import("@/test/code-api-mock")).makeCodeApiMock());

/**
 * What a screen reader is told by the conversation screen.
 *
 * Three things were wrong and none of them showed on screen: the composer carried `aria-expanded`
 * on a bare textbox (not allowed, ignored), the transcript was a `log` — a polite live region by
 * default, so the answer was read out token by token while a comment said it was not — and a
 * pending approval appeared in silence.
 */
const ASKED: CodeApprovalEvent = {
  id: "q-a11y",
  action: "write_file(path='notes.md')",
  reason: "write_file is restricted after this run consumed untrusted content",
  asked_at: Date.now() / 1000,
  wait_seconds: 300,
};

describe("Code — what assistive technology hears", () => {
  beforeEach(() => {
    vi.mocked(getFsTree).mockResolvedValue(emptyTree());
    vi.mocked(getGitStatus).mockResolvedValue(gitStatus());
    vi.mocked(getRuns).mockResolvedValue([]);
    vi.mocked(getPostureFacts).mockResolvedValue(postureFacts());
  });

  it("carries only ARIA a textbox supports: no aria-expanded without a combobox role", () => {
    renderWithProviders(<Code />);
    const box = screen.getByPlaceholderText(/^Ask about this code/);
    // `aria-expanded` is not allowed on a textbox (axe aria-allowed-attr) and a screen reader drops it.
    expect(box).not.toHaveAttribute("aria-expanded");
    expect(box).not.toHaveAttribute("role", "combobox");
    expect(box).toHaveAttribute("aria-autocomplete", "list");
  });

  it("does not announce the transcript as it grows: the log is explicitly not live", () => {
    renderWithProviders(<Code />);
    const log = screen.getByRole("log");
    // `log` is polite by default; only an explicit `off` stops the token-by-token reading.
    expect(log).toHaveAttribute("aria-live", "off");
  });

  it("announces the turn's STATE in one polite status, not its text", async () => {
    const user = userEvent.setup();
    vi.mocked(streamCodeTurn).mockImplementation(scriptTurn({ done: { answer: "a long final answer" } }));
    renderWithProviders(<Code />);

    const chip = screen.getByTestId("conversation-status");
    expect(chip).toHaveAttribute("aria-live", "polite");
    expect(chip).toHaveAttribute("aria-atomic", "true");
    // Before any turn there is nothing complete to say. This used to assert "Complete" on a fresh
    // conversation, which pinned the bug: a screen reader was told a turn had finished that never ran.
    expect(chip.textContent).toBe("");
    expect(chip).toHaveClass("hidden");

    await user.type(screen.getByPlaceholderText(/^Ask about this code/), "go");
    await user.click(screen.getByRole("button", { name: "Send" }));
    await screen.findByText(/a long final answer/);
    // The state vocabulary, short phrases only: never the answer.
    expect(chip.textContent).toBe("Complete");
    expect(chip).not.toHaveClass("hidden");
    expect(chip.textContent).not.toMatch(/answer/);
    expect(chip.closest("[aria-live='off']")).toBeNull();
  });

  it("announces a pending approval through an assertive alert, once", async () => {
    const user = userEvent.setup();
    vi.mocked(streamCodeTurn).mockImplementation(
      scriptTurn({ tokens: ["Writing notes."], approval: ASKED, parked: true }),
    );
    renderWithProviders(<Code />);
    await user.type(screen.getByPlaceholderText(/^Ask about this code/), "write it");
    await user.click(screen.getByRole("button", { name: "Send" }));

    const card = await screen.findByRole("group", { name: /decision/i });
    const alert = within(card).getByRole("alert");
    expect(alert).toHaveClass("sr-only");
    expect(alert.textContent).toContain("Chimera needs a decision");
    expect(alert.textContent).toContain(ASKED.reason);
    expect(within(card).getAllByRole("alert")).toHaveLength(1);
  });

  it("announces a FAILED turn with its reason, and never says Complete for it", async () => {
    const user = userEvent.setup();
    vi.mocked(streamCodeTurn).mockImplementation(async (_req, h) => {
      h.onSession?.("s1");
      h.onError?.("invalid_api_key");
    });
    renderWithProviders(<Code />);
    const chip = screen.getByTestId("conversation-status");
    await user.type(screen.getByPlaceholderText(/^Ask about this code/), "go");
    await user.click(screen.getByRole("button", { name: "Send" }));

    await screen.findByText("Failed: invalid API key", { selector: "[data-testid='conversation-status']" });
    expect(chip).toHaveAttribute("aria-live", "polite");
    expect(chip.textContent).not.toMatch(/complete/i);
    // The error text inside the silenced log is not the announcement; the chip is outside it.
    expect(chip.closest("[aria-live='off']")).toBeNull();
  });

  it("names an unrecognised failure by its own short text", async () => {
    const user = userEvent.setup();
    vi.mocked(streamCodeTurn).mockImplementation(async (_req, h) => {
      h.onError?.("rate_limit");
    });
    renderWithProviders(<Code />);
    await user.type(screen.getByPlaceholderText(/^Ask about this code/), "go");
    await user.click(screen.getByRole("button", { name: "Send" }));
    expect(await screen.findByText("Failed: rate_limit")).toBeInTheDocument();
  });

  it("announces a pending approval once, even when the card is minimised and opened again", async () => {
    const user = userEvent.setup();
    vi.mocked(streamCodeTurn).mockImplementation(
      scriptTurn({ tokens: ["Writing notes."], approval: ASKED, parked: true }),
    );
    renderWithProviders(<Code />);
    await user.type(screen.getByPlaceholderText(/^Ask about this code/), "write it");
    await user.click(screen.getByRole("button", { name: "Send" }));
    await screen.findByRole("group", { name: /decision/i });
    // The alert from the first mount is already in the document.

    await user.click(screen.getByRole("button", { name: "Minimize Approval" }));
    await user.click(await screen.findByRole("button", { name: "Expand Approval" }));
    const card = await screen.findByRole("group", { name: /decision/i });
    // Remounted, and it must NOT raise the assertive alert a second time.
    expect(within(card).queryByRole("alert")).not.toBeInTheDocument();
  });

  it("tells the status bar the conversation's chip is on screen, and takes it back on unmount", () => {
    function Probe() {
      return <span data-testid="probe">{String(useAgent().chatAnnounces)}</span>;
    }
    const { unmount } = renderWithProviders(
      <AgentProvider>
        <Code />
        <Probe />
      </AgentProvider>,
    );
    expect(screen.getByTestId("probe")).toHaveTextContent("true");
    unmount();
  });
});
