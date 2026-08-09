import { readFileSync } from "node:fs";
import { join } from "node:path";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AgentRegistry } from "@/components/AgentRegistry";
import { renderWithProviders as render } from "@/test/utils";

/**
 * The registry had an API and no screen.
 *
 * `getAgentRegistry`, `putAgent` and `deleteAgent` shipped alongside a board that dispatches by
 * lane, and had zero references in `src/components/`. The lane was a free text box guessing against
 * a list the app never showed, and the first news that an id was wrong was "0 worked" after a
 * dispatch — the mechanism-without-a-surface defect the v0.42.0 series existed to close, committed
 * in the last commit of that series.
 */
vi.mock("@/lib/api", () => ({
  getAgentRegistry: vi.fn(),
  putAgent: vi.fn(),
  deleteAgent: vi.fn(),
}));

const api = await import("@/lib/api");

const agent = (id: string, over: Record<string, unknown> = {}) => ({
  id,
  name: "",
  instructions: "",
  model: "",
  allowed_tools: [] as string[],
  ...over,
});

beforeEach(() => vi.clearAllMocks());

describe("the agent registry has a screen", () => {
  it("lists what the board can dispatch to", async () => {
    vi.mocked(api.getAgentRegistry).mockResolvedValue([
      agent("reviewer", { name: "Reviewer", model: "gpt-5-mini" }),
      agent("fixer"),
    ]);

    render(<AgentRegistry embedded />);

    expect(await screen.findByText("reviewer")).toBeInTheDocument();
    expect(screen.getByText("fixer")).toBeInTheDocument();
    expect(screen.getByText("gpt-5-mini")).toBeInTheDocument();
  });

  it("says an empty registry is fine, because it is", async () => {
    // Dispatch falls back to the built-in runner, so nothing is broken. Without the sentence this
    // reads as a failed load.
    vi.mocked(api.getAgentRegistry).mockResolvedValue([]);
    render(<AgentRegistry embedded />);
    expect(await screen.findByText(/no agents yet/i)).toBeInTheDocument();
  });

  it("reads an empty tool list as every tool, not as none", async () => {
    // Empty means NO RESTRICTION in this project's configuration, everywhere. A badge that said
    // "0 tools" would be the screen contradicting the server.
    vi.mocked(api.getAgentRegistry).mockResolvedValue([agent("free"), agent("narrow", { allowed_tools: ["read_file"] })]);
    render(<AgentRegistry embedded />);
    expect(await screen.findByText("all tools")).toBeInTheDocument();
    expect(screen.getByText("1 tools")).toBeInTheDocument();
  });

  it("creates an agent, and shows the list the server returned", async () => {
    vi.mocked(api.getAgentRegistry).mockResolvedValue([]);
    vi.mocked(api.putAgent).mockResolvedValue([agent("reviewer")]);
    const user = userEvent.setup();

    render(<AgentRegistry embedded />);
    await user.click(await screen.findByRole("button", { name: /new agent/i }));
    await user.type(screen.getByLabelText(/^id$/i), "reviewer");
    await user.click(screen.getByRole("button", { name: /^save$/i }));

    await waitFor(() => expect(api.putAgent).toHaveBeenCalledWith(expect.objectContaining({ id: "reviewer" })));
    // The list comes from the response, not from optimistic local state: the server owns the
    // registry, and rendering a guess would be the screen telling a story the server has not agreed to.
    expect(await screen.findByText("reviewer")).toBeInTheDocument();
  });

  it("surfaces a failure instead of pretending it saved", async () => {
    vi.mocked(api.getAgentRegistry).mockResolvedValue([]);
    vi.mocked(api.putAgent).mockRejectedValue(new Error("id already taken"));
    const user = userEvent.setup();

    render(<AgentRegistry embedded />);
    await user.click(await screen.findByRole("button", { name: /new agent/i }));
    await user.type(screen.getByLabelText(/^id$/i), "reviewer");
    await user.click(screen.getByRole("button", { name: /^save$/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent("id already taken");
  });

  it("deletes by id", async () => {
    vi.mocked(api.getAgentRegistry).mockResolvedValue([agent("gone")]);
    vi.mocked(api.deleteAgent).mockResolvedValue([]);
    const user = userEvent.setup();

    render(<AgentRegistry embedded />);
    await user.click(await screen.findByRole("button", { name: /delete gone/i }));

    await waitFor(() => expect(api.deleteAgent).toHaveBeenCalledWith("gone"));
  });
});

describe("no API without a surface", () => {
  it("keeps every agent-registry call reachable from a component", () => {
    // The check that would have caught the original defect. An exported client function that no
    // component calls is a feature the product has and the user cannot reach — and it costs nothing
    // to notice, as long as something looks.
    const root = join(process.cwd(), "src");
    const components = readFileSync(join(root, "components", "AgentRegistry.tsx"), "utf8");
    for (const fn of ["getAgentRegistry", "putAgent", "deleteAgent"]) {
      expect(components, `${fn} has no caller in the UI`).toContain(fn);
    }
  });
});
