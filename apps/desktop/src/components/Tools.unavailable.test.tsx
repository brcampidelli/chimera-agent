import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Tools } from "@/components/Tools";
import { getConfig, getTools, patchConfig } from "@/lib/api";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", () => ({
  getTools: vi.fn(),
  getConfig: vi.fn(),
  patchConfig: vi.fn(async () => ({ updated: ["CHIMERA_DECIDE_TOOL"] })),
}));

const DATA = {
  count: 1,
  tools: [
    { name: "read_file", description: "Read a file.", params: ["path"], tags: ["read"], untrusted_output: false },
  ],
  unavailable: [
    {
      name: "decide", description: "Ask typed questions.", kind: "setting",
      variables: ["CHIMERA_DECIDE_TOOL"], requires: "", switchable: true, default_on: false,
    },
    {
      name: "todo_write", description: "Record the task list.", kind: "setting",
      variables: ["CHIMERA_TODO_LIST"], requires: "", switchable: true, default_on: true,
    },
    {
      name: "web_search", description: "Search the web.", kind: "key",
      variables: ["TAVILY_API_KEY"], requires: "", switchable: false, default_on: false,
    },
    {
      name: "browser", description: "Drive a browser.", kind: "package",
      variables: [], requires: "playwright", switchable: false, default_on: false,
    },
  ],
};

/**
 * The screen could switch a tool OFF and never ON: it listed only what the registry held, so a tool
 * behind a condition was invisible exactly when it was off. The absent ones are listed now, with the
 * reason, and a switch only where turning it on is a setting.
 */
describe("Tools — a tool that is off can be turned on", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(getTools).mockResolvedValue(DATA as never);
    vi.mocked(getConfig).mockResolvedValue({ autonomy: { denied_tools: [] } } as never);
  });

  it("switching an off-by-default tool on writes its setting and says when it applies", async () => {
    const user = userEvent.setup();
    renderWithProviders(<Tools />);

    const sw = await screen.findByRole("switch", { name: "decide" });
    expect(sw).not.toBeChecked();
    await user.click(sw);

    await waitFor(() => expect(patchConfig).toHaveBeenCalledWith({ CHIMERA_DECIDE_TOOL: "1" }));
    expect(await screen.findByRole("status")).toHaveTextContent(/next message/i);
  });

  it("a tool that needs a key or a package names it and offers no switch", async () => {
    renderWithProviders(<Tools />);
    await screen.findByText("web_search");

    expect(screen.queryByRole("switch", { name: "web_search" })).not.toBeInTheDocument();
    expect(screen.queryByRole("switch", { name: "browser" })).not.toBeInTheDocument();
    expect(screen.getByText(/TAVILY_API_KEY/)).toBeInTheDocument();
    expect(screen.getByText(/playwright/)).toBeInTheDocument();
    expect(screen.getByText("needs a key")).toBeInTheDocument();
    expect(screen.getByText("needs a package")).toBeInTheDocument();
  });

  it("a default-on tool someone switched off is not called off by default", async () => {
    renderWithProviders(<Tools />);
    await screen.findByText("todo_write");
    expect(screen.getByText("switched off")).toBeInTheDocument();
    expect(screen.getAllByText("off by default")).toHaveLength(1); // decide only
  });
});
