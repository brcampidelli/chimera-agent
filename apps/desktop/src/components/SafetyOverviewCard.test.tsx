import { screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { SafetyOverviewCard } from "@/components/SafetyOverviewCard";
import { getSecurityOverview } from "@/lib/api";
import type { SafetyOverview } from "@/lib/types";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", () => ({
  getSecurityOverview: vi.fn(),
}));

const CARD: SafetyOverview = {
  floor: ["kernel_block", "secret_file", "secret_disclosure", "encoded_powershell", "default_branch_rewrite", "outside_reach"],
  floor_lexical: ["outside_reach"],
  sandbox_configured: "auto",
  sandbox: "local",
  sandbox_isolated: false,
  governance: [
    { surface: "chat", kernel: "off", taint: true },
    { surface: "code", kernel: "observe", taint: true },
    { surface: "cron", kernel: "observe", taint: true },
    { surface: "bot", kernel: "observe", taint: true },
    { surface: "solve", kernel: "off", taint: false },
  ],
  bypass: [{ scope: "project", id: "C:/repo/shop", expires_at: null }],
};

/** The card renders what the server computed; it holds no table of its own (audit 2026-11, a07-14). */
describe("SafetyOverviewCard", () => {
  beforeEach(() => vi.clearAllMocks());

  it("shows the floor with its limit, the real sandbox, each place and the switches on", async () => {
    vi.mocked(getSecurityOverview).mockResolvedValue(CARD);
    renderWithProviders(<SafetyOverviewCard />);
    expect(await screen.findByText("What holds right now")).toBeInTheDocument();
    expect(screen.getByText(/Deleting or copying folders outside the project/)).toBeInTheDocument();
    expect(screen.getByText(/it lowers the risk, it does not remove it/)).toBeInTheDocument();
    expect(screen.getByText("On this computer, with no isolation")).toBeInTheDocument();
    expect(screen.getByText(/The setting asks for "auto"/)).toBeInTheDocument();
    expect(screen.getByText("Scheduled tasks")).toBeInTheDocument();
    expect(screen.getAllByText("Rules only record")).toHaveLength(3);
    expect(screen.getByText("Project C:/repo/shop")).toBeInTheDocument();
    expect(screen.getByText("until you turn it off")).toBeInTheDocument();
  });

  it("names a category this build does not know by its code, rather than dropping it", async () => {
    vi.mocked(getSecurityOverview).mockResolvedValue({ ...CARD, floor: ["something_new"], bypass: [] });
    renderWithProviders(<SafetyOverviewCard />);
    expect(await screen.findByText("something_new")).toBeInTheDocument();
    expect(screen.getByText("Off everywhere")).toBeInTheDocument();
  });
});
