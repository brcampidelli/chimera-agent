import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ProjectPackCard } from "@/components/code/ProjectPackCard";
import { acceptProjectPack, getProjectPack, revokeProjectPack } from "@/lib/api";
import { renderWithProviders } from "@/test/utils";
import type { ProjectPack } from "@/lib/types";

vi.mock("@/lib/api", () => ({
  getProjectPack: vi.fn(),
  acceptProjectPack: vi.fn(),
  revokeProjectPack: vi.fn(),
}));

function pack(over: Partial<ProjectPack> = {}): ProjectPack {
  return {
    enabled: true,
    present: true,
    error: "",
    digest: "d".repeat(64),
    accepted: false,
    changed: false,
    held: false,
    applied: false,
    skills: ["pdf-forms", "held"],
    mcp: ["github", "linear"],
    tools_deny: ["run_shell"],
    ignored: ["reach"],
    skills_kept: ["pdf-forms"],
    skills_hidden: ["supabase-admin"],
    skills_not_active: ["held"],
    mcp_kept: ["github"],
    mcp_hidden: ["supabase"],
    mcp_not_configured: ["linear"],
    tools_denied: ["run_shell"],
    ...over,
  };
}

/** Study 29, P7.6: the "In this project" card under the Code screen's project bar. */
describe("the project-pack card", () => {
  beforeEach(() => vi.clearAllMocks());

  it("renders nothing for a folder without a pack", async () => {
    vi.mocked(getProjectPack).mockResolvedValue(pack({ present: false }));
    const { container } = renderWithProviders(<ProjectPackCard workspace="C:/w" />);
    await waitFor(() => expect(getProjectPack).toHaveBeenCalledWith("C:/w"));
    expect(container).toBeEmptyDOMElement();
  });

  it("lists what is kept, hidden, clamped, denied and ignored — including the shell's cost", async () => {
    const user = userEvent.setup();
    vi.mocked(getProjectPack).mockResolvedValue(pack());
    renderWithProviders(<ProjectPackCard workspace="C:/w" />);

    await screen.findByText(/only after you accept it/i);
    expect(screen.getByText("pack not applied")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /details/i }));

    expect(screen.getByText("supabase-admin")).toBeInTheDocument();
    // Named and refused: listed as such, so a pack that asked for more than it may is visible.
    expect(screen.getByText(/not switched on \(not activated\)/i).parentElement).toHaveTextContent("held");
    expect(screen.getByText(/not configured \(not launched\)/i).parentElement).toHaveTextContent("linear");
    expect(screen.getByText(/a pack cannot set/i).parentElement).toHaveTextContent("reach");
    expect(screen.getByText(/cannot run the tests itself/i)).toBeInTheDocument();
  });

  it("accepts the exact file it showed, by its digest", async () => {
    const user = userEvent.setup();
    vi.mocked(getProjectPack).mockResolvedValue(pack());
    vi.mocked(acceptProjectPack).mockResolvedValue(pack({ accepted: true, applied: true }));
    renderWithProviders(<ProjectPackCard workspace="C:/w" />);

    await user.click(await screen.findByRole("button", { name: /accept this pack/i }));

    await waitFor(() => expect(acceptProjectPack).toHaveBeenCalledWith("C:/w", "d".repeat(64)));
  });

  it("says a changed pack does not apply, and offers to stop applying an accepted one", async () => {
    const user = userEvent.setup();
    vi.mocked(getProjectPack).mockResolvedValue(pack({ accepted: true, applied: true }));
    vi.mocked(revokeProjectPack).mockResolvedValue(pack());
    renderWithProviders(<ProjectPackCard workspace="C:/w" />);

    await screen.findByText("pack applied");
    await user.click(screen.getByRole("button", { name: /stop applying/i }));
    await waitFor(() => expect(revokeProjectPack).toHaveBeenCalledWith("C:/w"));
  });

  it("with packs off in Settings, says nothing applies", async () => {
    vi.mocked(getProjectPack).mockResolvedValue(pack({ enabled: false }));
    renderWithProviders(<ProjectPackCard workspace="C:/w" />);
    await screen.findByText(/project packs are off in Settings/i);
    expect(screen.getByText("pack not applied")).toBeInTheDocument();
  });

  it("shows why an unreadable pack is refused, and offers no accept", async () => {
    vi.mocked(getProjectPack).mockResolvedValue(pack({ error: "`skills` must be a list of names", digest: "e".repeat(64) }));
    renderWithProviders(<ProjectPackCard workspace="C:/w" />);
    await screen.findByText(/could not be read.*must be a list of names/i);
    expect(screen.queryByRole("button", { name: /accept this pack/i })).not.toBeInTheDocument();
  });

  it("names a changed pack as changed", async () => {
    vi.mocked(getProjectPack).mockResolvedValue(pack({ changed: true }));
    renderWithProviders(<ProjectPackCard workspace="C:/w" />);
    await screen.findByText(/changed since you accepted it/i);
  });

  // Review of P7.6: a change to the file never lifts the narrowing — the agent can write that file.
  it("says the accepted version still applies after the file changed, and offers to stop it", async () => {
    const user = userEvent.setup();
    vi.mocked(getProjectPack).mockResolvedValue(
      pack({ changed: true, held: true, applied: true, error: "the pack is not valid JSON: x" }),
    );
    vi.mocked(revokeProjectPack).mockResolvedValue(pack());
    renderWithProviders(<ProjectPackCard workspace="C:/w" />);

    await screen.findByText(/the version you accepted still applies/i);
    expect(screen.getByText("pack applied")).toBeInTheDocument();
    expect(screen.queryByText(/so it does not apply/i)).not.toBeInTheDocument();
    expect(screen.getByText(/not valid JSON/i)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /stop applying/i }));
    await waitFor(() => expect(revokeProjectPack).toHaveBeenCalledWith("C:/w"));
  });

  it("still shows a held pack whose file was deleted", async () => {
    vi.mocked(getProjectPack).mockResolvedValue(
      pack({ present: false, changed: true, held: true, applied: true, digest: "" }),
    );
    renderWithProviders(<ProjectPackCard workspace="C:/w" />);
    await screen.findByText(/the version you accepted still applies/i);
    expect(screen.getByRole("button", { name: /stop applying/i })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /accept this pack/i })).not.toBeInTheDocument();
  });

  it("names the runs an applied pack reaches, and the ones it does not", async () => {
    vi.mocked(getProjectPack).mockResolvedValue(pack({ accepted: true, applied: true }));
    renderWithProviders(<ProjectPackCard workspace="C:/w" />);
    await screen.findByText(/started from this app/i);
    expect(screen.getByText(/scheduled jobs, the terminal and the bots do not read packs/i)).toBeInTheDocument();
  });
});
