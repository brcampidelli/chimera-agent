import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { Mcp } from "@/components/Mcp";
import { addMcpServer, getConfig, getMcpCatalog, getMcpServers } from "@/lib/api";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", () => ({
  addMcpServer: vi.fn(),
  getConfig: vi.fn(),
  getMcpCatalog: vi.fn(),
  getMcpServers: vi.fn(),
  removeMcpServer: vi.fn(),
  testMcpServer: vi.fn(),
}));

/**
 * The MCP screen had one way in: type a command, an argument list and a set of environment variable
 * names, correctly, from a vendor's page. That is a transcription exercise with a silent failure
 * mode — a wrong argument gives you a server that never connects and says nothing — and it is why
 * the screen stayed at "0 configurados".
 *
 * A catalogue removes that and introduces a worse risk: a recommendation nobody verified LOOKS
 * verified from the screen. So the tests here are mostly about what the screen must keep saying —
 * what actually limits each server, whether it is the vendor's or a stranger's, and that a pick
 * fills the form rather than saving anything.
 */

const GITHUB = {
  id: "github",
  label: "GitHub",
  summary: "Read repositories, issues and pull requests.",
  runner: "docker",
  available: true,
  command: "docker",
  args: ["run", "-i", "--rm", "ghcr.io/github/github-mcp-server"],
  env: { GITHUB_READ_ONLY: "1" },
  secrets: [],
  containment: "Read-only is on, and the token never reaches Chimera.",
  official: true,
  docs: "https://github.com/github/github-mcp-server",
};

const BANCO = {
  id: "db-postgres",
  label: "PostgreSQL",
  summary: "Query a PostgreSQL database.",
  runner: "uvx",
  available: false,
  command: "uvx",
  args: ["--from", "mcp-alchemy==2026.8.1.2602", "mcp-alchemy"],
  env: {},
  secrets: [{ key: "DB_URL", hint: "postgresql://user:senha@localhost/base", source: "" }],
  containment: "No read-only mode — a DROP runs. The database user is the boundary.",
  official: false,
  docs: "https://github.com/runekaagaard/mcp-alchemy",
};

function render() {
  renderWithProviders(<Mcp />);
}

describe("the MCP catalogue", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(getMcpServers).mockResolvedValue({ servers: [], count: 0 } as never);
    vi.mocked(getConfig).mockResolvedValue({ mcp: { autoload: false } } as never);
    vi.mocked(getMcpCatalog).mockResolvedValue({ entries: [GITHUB, BANCO], count: 2 } as never);
  });

  it("fills the form instead of saving anything", async () => {
    // The whole point of a pre-fill over a one-click add: what is about to be written is on screen,
    // in the same fields a hand-written server uses, before anything is stored.
    const user = userEvent.setup();
    render();

    await user.click(await screen.findByRole("button", { name: /use this|usar este/i }));

    expect(addMcpServer, "picking an entry saved it without being asked").not.toHaveBeenCalled();
    await waitFor(() => expect(screen.getByDisplayValue("docker")).toBeInTheDocument());
    expect(screen.getByDisplayValue(/ghcr\.io\/github\/github-mcp-server/)).toBeInTheDocument();
    expect(screen.getByDisplayValue("GITHUB_READ_ONLY")).toBeInTheDocument();
  });

  it("leaves a secret empty rather than pre-filling an example", async () => {
    // An example value sitting in a password field is something somebody eventually saves by
    // accident, and `DB_URL` carries a database password.
    //
    // The entry picked here MUST be one that has a secret. The first version of this test clicked
    // whichever button existed — which was GitHub, which asks for nothing — so the assertion was
    // vacuous: it passed unchanged against a build that filled the field with the example. Caught
    // by sabotage, which is the only reason it is written this way.
    const user = userEvent.setup();
    vi.mocked(getMcpCatalog).mockResolvedValue({
      entries: [{ ...BANCO, available: true }],
      count: 1,
    } as never);
    render();

    await user.click(await screen.findByRole("button", { name: /use this|usar este/i }));

    await waitFor(() => expect(screen.getByDisplayValue("DB_URL")).toBeInTheDocument());
    expect(
      screen.queryByDisplayValue(/postgresql:\/\//),
      "the example connection string was pre-filled into the field that takes the password",
    ).toBeNull();
  });

  it("says what to install instead of offering a runner that is missing", async () => {
    // An entry that is simply absent teaches nothing; one that is present and then fails to connect
    // teaches the wrong thing. `uvx` is not on this machine, so the card says so.
    render();

    expect(await screen.findByText(/needs uvx|precisa de uvx/i)).toBeInTheDocument();
    const usar = screen.getAllByRole("button", { name: /use this|usar este/i });
    expect(usar, "the unavailable entry offered a button anyway").toHaveLength(1);
  });

  it("keeps saying what actually limits each server", async () => {
    // Not a "read-only" badge: for most of these the limit is the CREDENTIAL, and a one-word chip
    // would say the opposite of the truth for the database entries.
    render();

    expect(await screen.findByText(/token never reaches Chimera/i)).toBeInTheDocument();
    expect(screen.getByText(/a DROP runs/i)).toBeInTheDocument();
  });

  it("does not present a stranger's server as the vendor's", async () => {
    render();

    expect(await screen.findByText(/^official$|^oficial$/i)).toBeInTheDocument();
    expect(screen.getByText(/^community$|^comunidade$/i)).toBeInTheDocument();
  });

  it("warns that a value typed here is stored as text", async () => {
    render();

    expect(await screen.findByText(/plain text|texto puro/i)).toBeInTheDocument();
  });

  it("shows nothing at all when the catalogue is empty", async () => {
    // Guarding the guard: an empty catalogue must leave the screen as it was, not render a heading
    // over nothing. Also the shape an older backend returns.
    vi.mocked(getMcpCatalog).mockResolvedValue({ entries: [], count: 0 } as never);
    render();

    await screen.findByText(/add a server|adicionar um servidor/i);
    expect(screen.queryByText(/connect a service|conectar um serviço/i)).toBeNull();
  });
});
