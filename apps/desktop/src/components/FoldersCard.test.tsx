import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { FoldersCard } from "@/components/FoldersCard";
import {
  flagCodeProject,
  grantCodeProjectShell,
  listCodeProjects,
  listCodeSessions,
} from "@/lib/api";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", () => ({
  flagCodeProject: vi.fn(),
  grantCodeProjectShell: vi.fn(),
  listCodeProjects: vi.fn(),
  listCodeSessions: vi.fn(),
  migrateShellGrants: vi.fn(async () => ({ migrated: false, recorded: 0, projects: [] })),
  registerCodeProject: vi.fn(),
}));

/**
 * The folders card: the server's command grants, listed where they can be read and revoked.
 *
 * What matters is that the switch WRITES THE SERVER and shows what the server answered — the grant is
 * a record the turn is held to, and a switch that kept its own state could read "on" over a folder
 * the server never granted.
 */

const ROWS = [
  { path: "C:\\loja", alias: "a loja", shell_granted: true, granted_at: "2026-10-01T10:00:00+00:00" },
  { path: "C:\\blog", alias: "" },
  { path: "C:\\velho", alias: "", hidden: true },
];

describe("FoldersCard", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    vi.mocked(listCodeProjects).mockResolvedValue(ROWS);
    vi.mocked(listCodeSessions).mockResolvedValue([
      { id: "s1", title: "x", workspace: "C:\\conversa", turns: 1, updated_at: 5 },
    ] as Awaited<ReturnType<typeof listCodeSessions>>);
  });

  it("lists registered folders and folders with conversations, with the server's grant", async () => {
    renderWithProviders(<FoldersCard reach="" />);

    const loja = await screen.findByRole("switch", { name: /a loja/ });
    expect(loja).toHaveAttribute("aria-checked", "true");
    expect(screen.getByRole("switch", { name: /blog/ })).toHaveAttribute("aria-checked", "false");
    // A folder only a conversation knows about can be granted too.
    expect(screen.getByRole("switch", { name: /conversa/ })).toBeInTheDocument();
    // A removed folder is listed apart, to be restored — not among the switches.
    expect(screen.queryByRole("switch", { name: /velho/ })).toBeNull();
    expect(screen.getByText("C:\\velho")).toBeInTheDocument();
  });

  it("grants through the server and shows what the server answered", async () => {
    vi.mocked(grantCodeProjectShell).mockResolvedValue([
      ...ROWS.slice(0, 1),
      { path: "C:\\blog", alias: "", shell_granted: true, granted_at: "2026-10-03T00:00:00+00:00" },
      ROWS[2],
    ]);
    const user = userEvent.setup();
    renderWithProviders(<FoldersCard reach="" />);

    await user.click(await screen.findByRole("switch", { name: /blog/ }));

    expect(grantCodeProjectShell).toHaveBeenCalledWith("C:\\blog", true);
    await waitFor(() =>
      expect(screen.getByRole("switch", { name: /blog/ })).toHaveAttribute("aria-checked", "true"),
    );
  });

  it("removes a folder from the lists through the server", async () => {
    vi.mocked(flagCodeProject).mockResolvedValue(ROWS);
    const user = userEvent.setup();
    renderWithProviders(<FoldersCard reach="" />);

    const hides = await screen.findAllByRole("button", { name: /Remove from the lists|Remover das listas/ });
    await user.click(hides[0]);
    expect(flagCodeProject).toHaveBeenCalledWith(expect.any(String), { hidden: true });
  });

  it("says when the standing reach makes the switches moot", async () => {
    renderWithProviders(<FoldersCard reach="read_only" />);
    expect(await screen.findByText(/read-only|somente leitura/)).toBeInTheDocument();
  });
});
