import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Tools } from "@/components/Tools";
import { getTools } from "@/lib/api";
import { renderWithProviders } from "@/test/utils";
import type { ToolInfo, Tools as ToolsOut } from "@/lib/types";

vi.mock("@/lib/api", () => ({
  getTools: vi.fn(),
  // The screen reads the denylist now, so it can show which capabilities are switched off — and
  // let you switch them. Empty by default here: nothing in this suite is about the denylist, and a
  // fixture that quietly denied something would make an unrelated assertion fail for a hidden
  // reason.
  getConfig: vi.fn(async () => ({
    autonomy: { reach: "", approval: "", host_exec: "ask", denied_tools: [] },
  })),
  patchConfig: vi.fn(async () => ({ updated: ["CHIMERA_TOOL_DENYLIST"] })),
}));

const mockGetTools = vi.mocked(getTools);

function tool(over: Partial<ToolInfo> = {}): ToolInfo {
  return {
    name: "read_file",
    description: "Read a file from the workspace.",
    params: ["path"],
    tags: ["read"],
    untrusted_output: false,
    ...over,
  };
}

function toolsOut(tools: ToolInfo[]): ToolsOut {
  return { count: tools.length, tools };
}

describe("Tools", () => {
  beforeEach(() => {
    mockGetTools.mockReset();
  });

  it("lists each registered tool with its capability tags and params", async () => {
    mockGetTools.mockResolvedValue(
      toolsOut([
        tool(),
        tool({
          name: "run_shell",
          description: "Run a shell command.",
          params: ["command", "cwd"],
          tags: ["exec"],
        }),
      ]),
    );
    renderWithProviders(<Tools />);

    expect(await screen.findByText("read_file")).toBeInTheDocument();
    expect(screen.getByText("run_shell")).toBeInTheDocument();
    // The shipped English text for read_file, which is the schema itself — pinned to the registry
    // by tests/test_tool_descriptions_reach_the_user.py. The fixture's own description is ignored
    // for a tool the app has a description of, and that is the behaviour, not an accident.
    expect(
      screen.getByText("Read a UTF-8 text file from the workspace."),
    ).toBeInTheDocument();
    expect(screen.getByText("read")).toBeInTheDocument();
    expect(screen.getByText("exec")).toBeInTheDocument();
    // The heading reports the backend's real count, not the rendered row count.
    expect(screen.getByText("2 tools")).toBeInTheDocument();
  });

  it("flags a tool whose output is untrusted", async () => {
    mockGetTools.mockResolvedValue(
      toolsOut([
        tool({ name: "web_fetch", tags: ["network"], untrusted_output: true }),
      ]),
    );
    renderWithProviders(<Tools />);

    expect(await screen.findByText("untrusted output")).toBeInTheDocument();
  });

  it("says 'no parameters' rather than leaving a tool's params blank", async () => {
    mockGetTools.mockResolvedValue(
      toolsOut([tool({ name: "list_skills", params: [] })]),
    );
    renderWithProviders(<Tools />);

    expect(await screen.findByText("no parameters")).toBeInTheDocument();
  });

  it("shows the honest empty state when no tools are registered", async () => {
    mockGetTools.mockResolvedValue(toolsOut([]));
    renderWithProviders(<Tools />);

    expect(await screen.findByText("No tools registered.")).toBeInTheDocument();
  });

  it("distinguishes 'no search match' from 'no tools at all'", async () => {
    const user = userEvent.setup();
    mockGetTools.mockResolvedValue(toolsOut([tool()]));
    renderWithProviders(<Tools />);

    await user.type(
      await screen.findByPlaceholderText(/search/i),
      "nonexistent",
    );

    expect(screen.getByText("No tools match that search.")).toBeInTheDocument();
    expect(screen.queryByText("No tools registered.")).not.toBeInTheDocument();
    expect(screen.queryByText("read_file")).not.toBeInTheDocument();
  });

  it("filters by name and by description", async () => {
    const user = userEvent.setup();
    mockGetTools.mockResolvedValue(
      toolsOut([
        tool(),
        tool({
          name: "git_commit",
          description: "Commit staged paths.",
          tags: ["write"],
        }),
      ]),
    );
    renderWithProviders(<Tools />);

    const search = await screen.findByPlaceholderText(/search/i);
    await user.type(search, "git");
    expect(screen.getByText("git_commit")).toBeInTheDocument();
    expect(screen.queryByText("read_file")).not.toBeInTheDocument();

    await user.clear(search);
    await user.type(search, "staged paths");
    expect(screen.getByText("git_commit")).toBeInTheDocument();
    expect(screen.queryByText("read_file")).not.toBeInTheDocument();
  });

  it("shows an error with a retry when the request fails — never an endless spinner", async () => {
    mockGetTools.mockRejectedValue(new Error("HTTP 500"));
    renderWithProviders(<Tools />);

    // The terminal error state, not a spinner that hangs forever on `!data`.
    expect(await screen.findByText("Couldn't load this.")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /try again/i }),
    ).toBeInTheDocument();
  });

  it("recovers when the user hits Try again after a transient failure", async () => {
    const user = userEvent.setup();
    mockGetTools.mockRejectedValueOnce(new Error("HTTP 503"));
    mockGetTools.mockResolvedValueOnce(toolsOut([tool({ name: "read_file" })]));
    renderWithProviders(<Tools />);

    await user.click(await screen.findByRole("button", { name: /try again/i }));

    expect(await screen.findByText("read_file")).toBeInTheDocument();
  });

  it("says what a tool does in the reader's language, and keeps the identifiers", async () => {
    // The description the API returns is the tool's SCHEMA — the sentence the MODEL is shown when
    // it decides whether to call something — so it is English at the source and stays that way.
    // This screen has the other reader: someone deciding what their agent may do, who should not
    // have to read English to answer that. So the sentence is translated and the identifiers are
    // not: `read_file` and `path` are what the thing is CALLED, in the logs, in the docs and in the
    // call itself, and a translated copy of a name names nothing.
    localStorage.setItem("chimera.lang", "pt");
    try {
      mockGetTools.mockResolvedValue(toolsOut([tool()]));
      renderWithProviders(<Tools />);

      expect(await screen.findByText("1 ferramentas")).toBeInTheDocument();
      expect(
        screen.getByText(/Lê um arquivo de texto UTF-8 do workspace/),
      ).toBeInTheDocument();
      expect(
        screen.queryByText("Read a file from the workspace."),
      ).not.toBeInTheDocument();
      // The name and the parameter, untouched.
      expect(screen.getByText("read_file")).toBeInTheDocument();
      expect(screen.getByText("path")).toBeInTheDocument();
    } finally {
      localStorage.removeItem("chimera.lang");
    }
  });

  it("shows a tool it has no translation for exactly as the server described it", async () => {
    // Every MCP tool lands here: its description comes from somebody else's server and cannot be
    // known in advance. So does any tool added in a version newer than the translations. The
    // fallback has to be the server's own words — a blank row, or a key rendered raw as
    // `tools.desc.whatever`, would both be worse than English.
    localStorage.setItem("chimera.lang", "pt");
    try {
      mockGetTools.mockResolvedValue(
        toolsOut([
          tool({
            name: "acme_invoice_lookup",
            description: "Look up an invoice by id.",
          }),
        ]),
      );
      renderWithProviders(<Tools />);

      expect(
        await screen.findByText("Look up an invoice by id."),
      ).toBeInTheDocument();
      expect(screen.queryByText(/tools\.desc\./)).not.toBeInTheDocument();
    } finally {
      localStorage.removeItem("chimera.lang");
    }
  });
});
