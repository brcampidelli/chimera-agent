import { screen, waitFor, within } from "@testing-library/react";
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
 * Selecting several folders and acting on them at once. The bulk actions are the single-row calls,
 * run once per folder: what is pinned here is that every selected path gets its call, that one
 * refusal does not stop the others, and that it is counted.
 */

const ROWS = [
  { path: "C:\\a", alias: "" },
  { path: "C:\\b", alias: "", pinned: true },
  { path: "C:\\c", alias: "" },
  { path: "C:\\velho", alias: "", hidden: true },
  { path: "C:\\antigo", alias: "", hidden: true },
];

const bar = () => within(screen.getByRole("toolbar"));

describe("FoldersCard — bulk selection", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    vi.mocked(listCodeProjects).mockResolvedValue(ROWS);
    vi.mocked(listCodeSessions).mockResolvedValue([]);
    vi.mocked(flagCodeProject).mockResolvedValue(ROWS);
    vi.mocked(grantCodeProjectShell).mockResolvedValue(ROWS);
  });

  it("select all, then remove from the lists, calls the server once per folder", async () => {
    const user = userEvent.setup();
    renderWithProviders(<FoldersCard reach="" />);

    await user.click(await screen.findByRole("checkbox", { name: "Select all" }));
    expect(screen.getByText("3 selected")).toBeInTheDocument();
    await user.click(bar().getByRole("button", { name: "Remove from the lists" }));

    await waitFor(() => expect(flagCodeProject).toHaveBeenCalledTimes(3));
    for (const path of ["C:\\a", "C:\\b", "C:\\c"])
      expect(flagCodeProject).toHaveBeenCalledWith(path, { hidden: true });
  });

  it("shows some / all / none on the header box", async () => {
    const user = userEvent.setup();
    renderWithProviders(<FoldersCard reach="" />);
    const all = await screen.findByRole("checkbox", { name: "Select all" });
    expect(all).not.toBeChecked();
    expect(all).not.toBePartiallyChecked();

    await user.click(screen.getByRole("checkbox", { name: "Select a" }));
    expect(all).toHaveAttribute("aria-checked", "mixed");
    expect(all).toBePartiallyChecked();

    // Clicking a mixed box selects everything, and clicking again clears it.
    await user.click(all);
    expect(all).toBeChecked();
    await user.click(all);
    expect(all).not.toBeChecked();
    expect(screen.queryByRole("toolbar")).toBeNull();
  });

  it("pins only the folders that are not pinned yet", async () => {
    const user = userEvent.setup();
    renderWithProviders(<FoldersCard reach="" />);
    await user.click(await screen.findByRole("checkbox", { name: "Select all" }));
    await user.click(bar().getByRole("button", { name: "Pin to the top" }));

    await waitFor(() => expect(flagCodeProject).toHaveBeenCalledTimes(2));
    expect(flagCodeProject).not.toHaveBeenCalledWith("C:\\b", expect.anything());
  });

  it("counts failures and still applies the rest", async () => {
    vi.mocked(flagCodeProject).mockImplementation(async (path) => {
      if (path === "C:\\b") throw new Error("refused");
      return ROWS;
    });
    const user = userEvent.setup();
    renderWithProviders(<FoldersCard reach="" />);
    await user.click(await screen.findByRole("checkbox", { name: "Select all" }));
    await user.click(bar().getByRole("button", { name: "Remove from the lists" }));

    expect(await screen.findByText(/1 of 3 could not be changed/)).toBeInTheDocument();
    expect(flagCodeProject).toHaveBeenCalledTimes(3);
    // The one that failed stays selected, so trying again is one click.
    expect(screen.getByText("1 selected")).toBeInTheDocument();
  });

  it("asks before allowing commands in several folders", async () => {
    const user = userEvent.setup();
    renderWithProviders(<FoldersCard reach="" />);
    await user.click(await screen.findByRole("checkbox", { name: "Select all" }));
    await user.click(bar().getByRole("button", { name: "Allow commands" }));

    expect(grantCodeProjectShell).not.toHaveBeenCalled();
    expect(await screen.findByRole("heading", { name: /Allow commands in 3 folders/ })).toBeInTheDocument();
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Allow commands" }));

    await waitFor(() => expect(grantCodeProjectShell).toHaveBeenCalledTimes(3));
    expect(grantCodeProjectShell).toHaveBeenCalledWith("C:\\c", true);
  });

  it("revokes without asking", async () => {
    const user = userEvent.setup();
    vi.mocked(listCodeProjects).mockResolvedValue(ROWS.map((row) => ({ ...row, shell_granted: true })));
    renderWithProviders(<FoldersCard reach="" />);
    await user.click(await screen.findByRole("checkbox", { name: "Select all" }));
    await user.click(bar().getByRole("button", { name: "Revoke commands" }));

    await waitFor(() => expect(grantCodeProjectShell).toHaveBeenCalledTimes(3));
    expect(grantCodeProjectShell).toHaveBeenCalledWith("C:\\a", false);
  });

  it("restores the selected removed folders", async () => {
    const user = userEvent.setup();
    renderWithProviders(<FoldersCard reach="" />);
    await user.click(await screen.findByRole("checkbox", { name: "Select all removed folders" }));
    await user.click(screen.getByRole("button", { name: "Show selected again" }));

    await waitFor(() => expect(flagCodeProject).toHaveBeenCalledTimes(2));
    expect(flagCodeProject).toHaveBeenCalledWith("C:\\velho", { hidden: false });
    expect(flagCodeProject).toHaveBeenCalledWith("C:\\antigo", { hidden: false });
  });
});
