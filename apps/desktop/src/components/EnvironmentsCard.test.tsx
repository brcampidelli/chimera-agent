import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { EnvironmentsCard } from "@/components/EnvironmentsCard";
import {
  listCodeEnvironments,
  listWslDistros,
  removeCodeEnvironment,
  saveCodeEnvironment,
  type CodeEnvironments,
} from "@/lib/api";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  listCodeEnvironments: vi.fn(),
  listWslDistros: vi.fn(),
  saveCodeEnvironment: vi.fn(),
  removeCodeEnvironment: vi.fn(),
}));

const LOCAL = { id: "local", name: "Local", kind: "local", params: {}, builtin: true, available: true };
const UBUNTU = { id: "e1", name: "Ubuntu dev", kind: "wsl", params: { distro: "Ubuntu" }, builtin: false, available: true };

function listing(over: Partial<CodeEnvironments> = {}): CodeEnvironments {
  return { environments: [LOCAL], kinds: [{ kind: "wsl", available: true }], defaults: {}, ...over };
}

beforeEach(() => {
  vi.mocked(listCodeEnvironments).mockReset();
  vi.mocked(listWslDistros).mockReset().mockResolvedValue({ supported: true, distros: ["Ubuntu", "Debian"] });
  vi.mocked(saveCodeEnvironment).mockReset();
  vi.mocked(removeCodeEnvironment).mockReset();
});

describe("EnvironmentsCard", () => {
  it("says WSL is not isolation and where the files stay", async () => {
    vi.mocked(listCodeEnvironments).mockResolvedValue(listing());
    renderWithProviders(<EnvironmentsCard />);
    expect(await screen.findByText(/WSL is not isolation/)).toBeInTheDocument();
    expect(screen.getByText(/No saved environments/)).toBeInTheDocument();
  });

  it("saves a WSL environment with the distro that was picked", async () => {
    // The list the screen refetches after a save is the server's, so it answers with the new row.
    vi.mocked(listCodeEnvironments).mockResolvedValueOnce(listing()).mockResolvedValue(listing({ environments: [LOCAL, UBUNTU] }));
    vi.mocked(saveCodeEnvironment).mockResolvedValue(listing({ environments: [LOCAL, UBUNTU] }));
    renderWithProviders(<EnvironmentsCard />);
    await userEvent.type(await screen.findByLabelText("Name"), "Ubuntu dev");
    await waitFor(() => expect(screen.getByRole("option", { name: "Debian" })).toBeInTheDocument());
    await userEvent.selectOptions(screen.getByLabelText("Distro"), "Ubuntu");
    await userEvent.click(screen.getByRole("button", { name: "Add environment" }));
    await waitFor(() =>
      expect(saveCodeEnvironment).toHaveBeenCalledWith({ name: "Ubuntu dev", kind: "wsl", params: { distro: "Ubuntu" } }),
    );
    expect(await screen.findByText("Ubuntu dev")).toBeInTheDocument();
  });

  it("removes a saved environment", async () => {
    vi.mocked(listCodeEnvironments).mockResolvedValue(listing({ environments: [LOCAL, UBUNTU] }));
    vi.mocked(removeCodeEnvironment).mockResolvedValue(listing());
    renderWithProviders(<EnvironmentsCard />);
    await userEvent.click(await screen.findByRole("button", { name: "Remove: Ubuntu dev" }));
    await waitFor(() => expect(removeCodeEnvironment).toHaveBeenCalledWith("e1"));
  });

  it("offers no form where WSL cannot run", async () => {
    vi.mocked(listCodeEnvironments).mockResolvedValue(listing({ kinds: [{ kind: "wsl", available: false }] }));
    renderWithProviders(<EnvironmentsCard />);
    expect(await screen.findByText("WSL is available on Windows only.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Add environment" })).toBeNull();
  });

  it("says so when the server refuses the environment", async () => {
    vi.mocked(listCodeEnvironments).mockResolvedValue(listing());
    vi.mocked(saveCodeEnvironment).mockRejectedValue(new Error("no"));
    renderWithProviders(<EnvironmentsCard />);
    await userEvent.type(await screen.findByLabelText("Name"), "x");
    await userEvent.click(screen.getByRole("button", { name: "Add environment" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Could not save the environment.");
  });
});
