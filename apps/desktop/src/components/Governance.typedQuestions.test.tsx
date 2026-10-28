import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { Governance } from "@/components/Governance";
import { answerUserQuestion, getApprovals, getGovernanceAudit, getGovernanceInjection, getUserQuestions, type UserQuestionEvent } from "@/lib/api";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", () => ({
  getConfig: vi.fn(async () => ({ autonomy: { governance: "off" } })),
  getGovernanceAudit: vi.fn(),
  getGovernanceInjection: vi.fn(),
  getApprovals: vi.fn(async () => []),
  getUserQuestions: vi.fn(async () => []),
  answerUserQuestion: vi.fn(async () => ({ ok: true })),
  getSandboxState: vi.fn(),
  getBridgeFullControl: vi.fn(async () => ({ on: false, available: false, active: false, actions: [] })),
  getAccess: vi.fn(async () => ({
    server: { bind: "127.0.0.1", port: 8765, network: false },
    server_token: { set: false },
    bridge: { enabled: false, active: false, tier: null, hint: "" },
    sharing: { enabled: true, expiry_hours: null },
    guest_door: { open: false, port: null, urls: [] },
    links: [],
  })),
}));

/** A design approval as `getUserQuestions` maps it for the screen. */
function design(): UserQuestionEvent {
  return {
    id: "d1",
    kind: "design_approval",
    question: "Review the design and choose approval or adjustments.",
    options: ["approved", "adjustments"],
    allow_free_text: true,
    asked_at: Math.floor(Date.now() / 1000) - 12,
    wait_seconds: 300,
    description: "Launch screen",
    document_id: "doc-7",
    title: "Fornalha",
    revision: 12,
  };
}

/**
 * Governance lists what is waiting, typed questions included, and its list agrees with the count the
 * status bar shows: a question the owner is asked is never on one screen and missing from the other.
 */
describe("Governance — typed questions are part of what is waiting", () => {
  beforeEach(() => {
    vi.mocked(getGovernanceAudit).mockReset();
    vi.mocked(getGovernanceInjection).mockReset();
    vi.mocked(getApprovals).mockResolvedValue([]);
    vi.mocked(getUserQuestions).mockResolvedValue([]);
    vi.mocked(answerUserQuestion).mockReset();
    vi.mocked(answerUserQuestion).mockResolvedValue({ ok: true });
  });

  it("lists a design approval with Approve and Request changes, and answers it by text", async () => {
    vi.mocked(getUserQuestions).mockResolvedValueOnce([design()]).mockResolvedValue([]);
    const user = userEvent.setup();
    renderWithProviders(<Governance />);

    const approve = await screen.findByRole("button", { name: "Approve" });
    expect(screen.getByRole("button", { name: "Request changes" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /allow this once/i })).not.toBeInTheDocument();

    await user.click(approve);

    await waitFor(() => expect(answerUserQuestion).toHaveBeenCalledWith("d1", "approved"));
    await waitFor(() => expect(screen.queryByRole("button", { name: "Approve" })).not.toBeInTheDocument());
  });

  it("the list shows a yes-or-no question and a typed one together, as the count does", async () => {
    vi.mocked(getApprovals).mockResolvedValue([
      {
        id: "q1", action: "run_shell: ls", reason: "outside the folder", asked_at: 0, age_seconds: 10,
        decision: "review", p: null, band: "", decider_model: "", decision_id: "", run_id: "",
        kind: "", host: "", irreversible: false, preview: [], typed_text: "", session_id: "",
        workspace: "", work: "", suggestion: null,
      } as unknown as Awaited<ReturnType<typeof getApprovals>>[number],
    ]);
    vi.mocked(getUserQuestions).mockResolvedValue([design()]);
    renderWithProviders(<Governance />);

    expect(await screen.findByRole("button", { name: /allow this once/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Approve" })).toBeInTheDocument();
  });
});
