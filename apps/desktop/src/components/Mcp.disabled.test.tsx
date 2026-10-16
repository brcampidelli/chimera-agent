import { screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { Mcp } from "@/components/Mcp";
import { getConfig, getMcpCatalog, getMcpServers } from "@/lib/api";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", () => ({
  addMcpServer: vi.fn(),
  approveMcpManifest: vi.fn(),
  getConfig: vi.fn(),
  getMcpCatalog: vi.fn(),
  getMcpServers: vi.fn(),
  removeMcpServer: vi.fn(),
  testMcpServer: vi.fn(),
}));

/**
 * Study 33, P2-12. `chimera mcp disable` keeps a server configured but never connects or mounts it.
 * A paused server that looks like every other row reads as "configured and working", so the row
 * says it is disabled — and only that row.
 */

const server = (name: string, disabled: boolean) => ({
  name,
  command: "npx",
  args: [],
  env_keys: [],
  last_test: null,
  manifest_held: null,
  disabled,
});

describe("a disabled MCP server", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(getMcpServers).mockResolvedValue({
      servers: [server("paused", true), server("live", false)],
      count: 2,
    } as never);
    vi.mocked(getConfig).mockResolvedValue({ mcp: { autoload: true } } as never);
    vi.mocked(getMcpCatalog).mockResolvedValue({ entries: [], count: 0 } as never);
  });

  it("says so on its own row and nowhere else", async () => {
    renderWithProviders(<Mcp />);

    expect(await screen.findByText("paused")).toBeInTheDocument();
    expect(screen.getAllByText("Disabled")).toHaveLength(1);
  });
});
