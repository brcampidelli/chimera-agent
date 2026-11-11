import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { Automation } from "@/components/Automation";
import { MarkdownAgents } from "@/components/MarkdownAgents";
import { getAgentRegistry, getMarkdownAgents, listCodeProjects } from "@/lib/api";
import type { MarkdownAgents as Listing } from "@/lib/types";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", () => ({
  getMarkdownAgents: vi.fn(),
  listCodeProjects: vi.fn(),
  getAgentRegistry: vi.fn(),
  putAgent: vi.fn(),
  deleteAgent: vi.fn(),
  designAgent: vi.fn(),
}));

/**
 * Automação › Agentes lists the Markdown and plugin subagents beside the app's own (plugin plan B7).
 *
 * They were visible only in the console of `chimera solve --subagents`. The section is read only,
 * shows what a file does not say (the tools left after the fence, the model a project cannot pick),
 * keeps a refused file on screen with its reason, and says plainly that a Code turn cannot run them.
 */
const LISTING: Listing = {
  runs_in_code: false,
  agents: [
    {
      id: "planner",
      description: "Plans the work",
      source: "owner",
      file: "global:agents/planner.md",
      model: "deepseek/deepseek-chat",
      model_ignored: false,
      declared_tools: ["read_file", "not_a_tool"],
      effective_tools: ["read_file"],
      steps: 4,
      origin: "chimera",
      plan_only: false,
      inherits_tools: false,
    },
    {
      id: "my-plugin:reviewer",
      description: "Reviews code",
      source: "plugin:my-plugin",
      file: "plugin:my-plugin/agents/reviewer.md",
      model: null,
      model_ignored: true,
      declared_tools: ["read_file", "grep"],
      effective_tools: ["read_file", "grep"],
      steps: 12,
      origin: "plugin",
      plan_only: false,
      inherits_tools: false,
    },
    {
      id: "code-reviewer",
      description: "Reviews code for quality",
      source: "project",
      file: "project:.claude/agents/code-reviewer.md",
      model: "openrouter/mid-model",
      model_ignored: false,
      model_role: "mid",
      memory: "project",
      declared_tools: [],
      effective_tools: ["read_file", "grep"],
      steps: 8,
      origin: "claude",
      plan_only: true,
      inherits_tools: true,
    },
  ],
  issues: [
    { path: "project:.chimera/agents/bad.md", message: "expected YAML frontmatter", agent_id: "bad" },
  ],
};

describe("the Markdown agents section", () => {
  beforeEach(() => {
    vi.mocked(getMarkdownAgents).mockReset().mockResolvedValue(LISTING);
    vi.mocked(listCodeProjects).mockReset().mockResolvedValue([
      { path: "/proj/a", alias: "Alpha" },
      { path: "/proj/hidden", alias: "", hidden: true },
    ]);
    vi.mocked(getAgentRegistry).mockReset().mockResolvedValue([]);
  });

  it("lists each agent with its source, model rule, tools and what the fence removed", async () => {
    renderWithProviders(<MarkdownAgents />);
    const planner = (await screen.findByText("planner")).closest("li") as HTMLElement;
    expect(within(planner).getByText("yours")).toBeInTheDocument();
    expect(within(planner).getByText("deepseek/deepseek-chat")).toBeInTheDocument();
    expect(within(planner).getByText("Tools here: read_file")).toBeInTheDocument();
    expect(within(planner).getByText("Named but not available here: not_a_tool")).toBeInTheDocument();

    const reviewer = screen.getByText("my-plugin:reviewer").closest("li") as HTMLElement;
    expect(within(reviewer).getByText("plugin my-plugin")).toBeInTheDocument();
    expect(within(reviewer).getByText("model ignored")).toBeInTheDocument();
    expect(within(reviewer).queryByText(/Named but not available/)).not.toBeInTheDocument();
  });

  it("shows where a Claude Code agent came from, its model role, memory and read-only mode", async () => {
    renderWithProviders(<MarkdownAgents />);
    const agent = (await screen.findByText("code-reviewer")).closest("li") as HTMLElement;
    expect(within(agent).getByText(".claude")).toBeInTheDocument();
    expect(within(agent).getByText("model role: mid")).toBeInTheDocument();
    expect(within(agent).getByText("openrouter/mid-model")).toBeInTheDocument();
    expect(within(agent).getByText("memory: project")).toBeInTheDocument();
    expect(within(agent).getByText("narrower: read-only tools")).toBeInTheDocument();
    expect(within(agent).getByText("uses the session's tools")).toBeInTheDocument();
    const planner = screen.getByText("planner").closest("li") as HTMLElement;
    expect(within(planner).getByText(".chimera")).toBeInTheDocument();
  });

  it("keeps a refused file on screen with its reason", async () => {
    renderWithProviders(<MarkdownAgents />);
    expect(await screen.findByText("Files that were shadowed or refused")).toBeInTheDocument();
    expect(screen.getByText(/expected YAML frontmatter/)).toBeInTheDocument();
  });

  it("says the Code conversation cannot run them yet", async () => {
    renderWithProviders(<MarkdownAgents />);
    expect(await screen.findByRole("note")).toHaveTextContent("chimera solve --subagents");
  });

  it("offers no way to edit or delete — the file owns the definition", async () => {
    renderWithProviders(<MarkdownAgents />);
    await screen.findByText("planner");
    const section = screen.getByRole("region", { name: "Agents from files and plugins" });
    expect(within(section).queryByRole("button")).not.toBeInTheDocument();
  });

  it("reads another project's agents when one is picked, and hides hidden projects", async () => {
    renderWithProviders(<MarkdownAgents />);
    await waitFor(() => expect(getMarkdownAgents).toHaveBeenCalledWith(""));
    const picker = screen.getByRole("combobox", { name: "Project" });
    await waitFor(() => expect(within(picker).getByText("Alpha")).toBeInTheDocument());
    expect(within(picker).queryByText("/proj/hidden")).not.toBeInTheDocument();
    await userEvent.selectOptions(picker, "/proj/a");
    await waitFor(() => expect(getMarkdownAgents).toHaveBeenCalledWith("/proj/a"));
  });

  it("sits in Automation › Agents after the agents saved in the app", async () => {
    renderWithProviders(<Automation tab="agents" onTabChange={() => {}} />);
    expect(await screen.findByRole("region", { name: "Agents from files and plugins" })).toBeInTheDocument();
    expect(getAgentRegistry).toHaveBeenCalled();
  });
});
