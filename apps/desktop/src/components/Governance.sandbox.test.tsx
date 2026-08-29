/** The Security screen now answers the question it was named after.
 *
 * It reported prompt-injection defence and the audit log, and said nothing about the boundary around
 * EXECUTION — the thing those two exist to defend. A person opening this screen to ask "what stops a
 * command from reaching my files?" got an answer about a different question entirely.
 *
 * The posture line above the composer does say it, and that was the whole surface: reachable only
 * after choosing a project and turning commands on, which is the moment it is already too late to be
 * reading it for the first time.
 */

import { screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { Governance } from "@/components/Governance";
import { getGovernanceAudit, getGovernanceInjection, getSandboxState } from "@/lib/api";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", () => ({
  getGovernanceInjection: vi.fn(),
  getGovernanceAudit: vi.fn(),
  getSandboxState: vi.fn(),
}));

const mockSandbox = vi.mocked(getSandboxState);

function state(over: Record<string, unknown> = {}) {
  return {
    configured: "auto",
    backend: "host",
    isolated: false,
    reason: "",
    platform: "Windows",
    ...over,
  } as never;
}

beforeEach(() => {
  vi.clearAllMocks();
  // The full shape, not a plausible subset: the screen reads `leaks_defended.length`, and a fixture
  // missing it throws before anything renders — so every assertion in this file would have failed
  // about a field none of them are testing.
  vi.mocked(getGovernanceInjection).mockResolvedValue({
    total_attacks: 6,
    defended_asr: 0.17,
    undefended_asr: 1,
    defended_block_rate: 0.83,
    undefended_block_rate: 0,
    by_category: [{ category: "exfil", defended_asr: 0.5, undefended_asr: 1, count: 2 }],
    attacks: [{
      id: "http_exfil", category: "exfil", harmful_tool: "http_get",
      blocked_defended: false, blocked_undefended: false,
    }],
    leaks_defended: ["http_exfil"],
    defense: "taint_narrowing",
    armed: true,
    trust_kernel: false,
  } as never);
  vi.mocked(getGovernanceAudit).mockResolvedValue({
    events: [], count: 0, populated: false, chain: null,
  } as never);
});

describe("Governance — the execution boundary", () => {
  it("says commands would run on this machine, and why", async () => {
    mockSandbox.mockResolvedValue(
      state({ reason: "Windows has no OS sandbox in Chimera." }),
    );

    renderWithProviders(<Governance />);

    expect(await screen.findByText(/would run on this machine/i)).toBeInTheDocument();
    expect(screen.getByText(/Windows has no OS sandbox/i)).toBeInTheDocument();
  });

  it("says what still applies, so 'no jail' does not read as 'no protection'", async () => {
    // The governance kernel, the write region and the confirmation prompt are not nothing. A panel
    // that only reports the absence teaches the reader that the screen has nothing to offer.
    mockSandbox.mockResolvedValue(state());

    renderWithProviders(<Governance />);

    expect(await screen.findByText(/still apply/i)).toBeInTheDocument();
  });

  it("reports what was ASKED FOR beside what is ACTUALLY used", async () => {
    // The two differ exactly when it matters. Showing only the setting would report an intention,
    // and "I thought it was sandboxed" is what an intention reported as a fact produces.
    mockSandbox.mockResolvedValue(state({ configured: "auto", backend: "host" }));

    renderWithProviders(<Governance />);

    expect(await screen.findByText(/asked for: auto/i)).toBeInTheDocument();
    expect(screen.getByText(/actually: host/i)).toBeInTheDocument();
  });

  it("says so plainly when a kernel boundary really does apply", async () => {
    mockSandbox.mockResolvedValue(
      state({ isolated: true, backend: "bubblewrap", platform: "Linux", reason: "" }),
    );

    renderWithProviders(<Governance />);

    expect(await screen.findByText(/inside a kernel sandbox/i)).toBeInTheDocument();
    expect(screen.queryByText(/still apply/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/would run on this machine/i)).not.toBeInTheDocument();
  });

  it("a failed probe is an error to retry, never a silent 'you are isolated'", async () => {
    // The dangerous default. An unreachable endpoint must not render as the reassuring state.
    mockSandbox.mockRejectedValue(new Error("boom"));

    renderWithProviders(<Governance />);

    // Wait for the panel to settle on SOMETHING, then assert the reassuring sentence is not it.
    await screen.findByText(/Commands on this machine/i);
    expect(screen.queryByText(/inside a kernel sandbox/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/would run on this machine/i)).not.toBeInTheDocument();
  });
});
