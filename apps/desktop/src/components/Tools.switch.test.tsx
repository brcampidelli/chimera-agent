import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Tools } from "@/components/Tools";
import { getConfig, getTools, patchConfig } from "@/lib/api";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", () => ({
  getTools: vi.fn(),
  getConfig: vi.fn(),
  patchConfig: vi.fn(async () => ({ updated: ["CHIMERA_TOOL_DENYLIST"] })),
}));

const TOOLS = {
  count: 2,
  tools: [
    {
      name: "read_file",
      description: "Read a file.",
      params: ["path"],
      tags: ["read"],
      untrusted_output: false,
    },
    {
      name: "run_shell",
      description: "Run a command.",
      params: ["command"],
      tags: ["exec"],
      untrusted_output: false,
    },
  ],
};

function config(denied: string[]) {
  return { autonomy: { reach: "", approval: "", host_exec: "ask", denied_tools: denied } };
}

/**
 * This screen was an honest inventory of what the agent can do that let you change none of it. The
 * only route to "my agent must not run shell commands" was hand-editing CHIMERA_TOOL_DENYLIST — and
 * until this release that variable reached `chimera run` and `chimera solve` and nothing else, so
 * even the hand-edit did nothing to the app.
 */
describe("Tools — switching a capability off", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(getTools).mockResolvedValue(TOOLS as never);
    vi.mocked(getConfig).mockResolvedValue(config([]) as never);
  });

  it("adds the tool to the denylist without dropping the rest", async () => {
    const user = userEvent.setup();
    vi.mocked(getConfig).mockResolvedValue(config(["browser"]) as never);
    renderWithProviders(<Tools />);

    await user.click(await screen.findByRole("switch", { name: "run_shell" }));

    await waitFor(() => expect(patchConfig).toHaveBeenCalledOnce());
    expect(vi.mocked(patchConfig).mock.calls[0][0]).toEqual({
      CHIMERA_TOOL_DENYLIST: "browser,run_shell",
    });
  });

  it("keeps names this build does not have", async () => {
    // A denylist naming a tool that arrives with a later version should still deny it. Rebuilding
    // the list from the tools on screen would turn "I switched this off" into "I switched this off
    // until an upgrade" — and nothing would say when it came back.
    const user = userEvent.setup();
    vi.mocked(getConfig).mockResolvedValue(config(["from_a_future_release"]) as never);
    renderWithProviders(<Tools />);

    await user.click(await screen.findByRole("switch", { name: "read_file" }));

    await waitFor(() => expect(patchConfig).toHaveBeenCalledOnce());
    expect(vi.mocked(patchConfig).mock.calls[0][0].CHIMERA_TOOL_DENYLIST).toContain(
      "from_a_future_release",
    );
  });

  it("shows a denied tool as off", async () => {
    vi.mocked(getConfig).mockResolvedValue(config(["run_shell"]) as never);
    renderWithProviders(<Tools />);

    expect(await screen.findByRole("switch", { name: "run_shell" })).not.toBeChecked();
    expect(screen.getByRole("switch", { name: "read_file" })).toBeChecked();
  });

  it("turning one back on removes only that name", async () => {
    const user = userEvent.setup();
    vi.mocked(getConfig).mockResolvedValue(config(["read_file", "run_shell"]) as never);
    renderWithProviders(<Tools />);

    await user.click(await screen.findByRole("switch", { name: "run_shell" }));

    await waitFor(() => expect(patchConfig).toHaveBeenCalledOnce());
    expect(vi.mocked(patchConfig).mock.calls[0][0]).toEqual({
      CHIMERA_TOOL_DENYLIST: "read_file",
    });
  });
});
