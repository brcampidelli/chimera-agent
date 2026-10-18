import { fireEvent, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { Mcp } from "@/components/Mcp";
import {
  connectMcpOAuth,
  disconnectMcpOAuth,
  getConfig,
  getMcpCatalog,
  getMcpServers,
  testMcpServer,
} from "@/lib/api";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", () => ({
  addMcpServer: vi.fn(),
  approveMcpManifest: vi.fn(),
  connectMcpOAuth: vi.fn(),
  disconnectMcpOAuth: vi.fn(),
  getConfig: vi.fn(),
  getMcpCatalog: vi.fn(),
  getMcpServers: vi.fn(),
  removeMcpServer: vi.fn(),
  testMcpServer: vi.fn(),
}));

/**
 * M1: a remote MCP server that signs in with OAuth gets Connect on its row; once signed in, the row
 * says as whom (only when the server said) and offers Disconnect. A server that does not sign in
 * with OAuth gets neither — a button there would promise a sign-in that does not exist.
 */

const server = (name: string, oauth: { connected: boolean; identity?: string | null } | null) => ({
  name,
  command: "",
  args: [],
  env_keys: [],
  last_test: null,
  manifest_held: null,
  disabled: false,
  url_host: oauth ? `mcp.${name}.test` : null,
  oauth,
});

function list(...servers: ReturnType<typeof server>[]) {
  vi.mocked(getMcpServers).mockResolvedValue({ servers, count: servers.length } as never);
}

describe("an MCP server that signs in with OAuth", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(getConfig).mockResolvedValue({ mcp: { autoload: true } } as never);
    vi.mocked(getMcpCatalog).mockResolvedValue({ entries: [], count: 0 } as never);
  });

  it("offers Connect only on the row that needs it, and Connect starts the sign-in", async () => {
    list(server("notion", { connected: false }), server("files", null));
    vi.mocked(connectMcpOAuth).mockResolvedValue({
      ok: true,
      error: null,
      oauth: { connected: true, identity: "bruno@example.test" },
    } as never);
    renderWithProviders(<Mcp />);

    const connect = await screen.findAllByRole("button", { name: "Connect" });
    expect(connect).toHaveLength(1);
    expect(screen.getByText("mcp.notion.test")).toBeInTheDocument();
    fireEvent.click(connect[0]);
    await waitFor(() => expect(connectMcpOAuth).toHaveBeenCalledWith("notion"));
  });

  it("says as whom it is connected, and Disconnect signs out", async () => {
    list(server("linear", { connected: true, identity: "bruno@example.test" }));
    vi.mocked(disconnectMcpOAuth).mockResolvedValue({
      ok: true,
      revoked: true,
      error: null,
      oauth: { connected: false, identity: null },
    } as never);
    renderWithProviders(<Mcp />);

    expect(await screen.findByText("Connected as bruno@example.test")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Connect" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Disconnect" }));
    await waitFor(() => expect(disconnectMcpOAuth).toHaveBeenCalledWith("linear"));
  });

  it("says plainly why a refused sign-in was refused", async () => {
    list(server("notion", { connected: false }));
    vi.mocked(connectMcpOAuth).mockResolvedValue({
      ok: false,
      error: "issuer_mismatch",
      oauth: { connected: false, identity: null },
    } as never);
    renderWithProviders(<Mcp />);

    fireEvent.click(await screen.findByRole("button", { name: "Connect" }));
    expect(
      await screen.findByText(
        "The sign-in server is not the one this MCP server named, so the sign-in was refused.",
      ),
    ).toBeInTheDocument();
  });

  it("learns from a Test that a server asks for a sign-in", async () => {
    list(server("remote", null));
    vi.mocked(testMcpServer).mockResolvedValue({
      ok: false,
      tools: [],
      error: "MCP server 'remote' precisa conectar no app",
      needs_sign_in: true,
    } as never);
    renderWithProviders(<Mcp />);

    expect(screen.queryByRole("button", { name: "Connect" })).not.toBeInTheDocument();
    fireEvent.click(await screen.findByRole("button", { name: "Test" }));
    expect(await screen.findByRole("button", { name: "Connect" })).toBeInTheDocument();
  });
});
