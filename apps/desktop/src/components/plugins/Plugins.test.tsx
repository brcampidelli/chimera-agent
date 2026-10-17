import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { Plugins } from "@/components/plugins/Plugins";
import { settingsTab } from "@/components/Settings";
import {
  applyPluginUpdate,
  checkPluginUpdate,
  disablePlugin,
  enablePlugin,
  getPluginMarketplaceEntries,
  getPluginMarketplaces,
  getPlugins,
  installPlugin,
} from "@/lib/api";
import { renderWithProviders } from "@/test/utils";
import type { Plugin } from "@/lib/types";

vi.mock("@/lib/api", () => ({
  getPlugins: vi.fn(),
  enablePlugin: vi.fn(),
  disablePlugin: vi.fn(),
  checkPluginUpdate: vi.fn(),
  applyPluginUpdate: vi.fn(),
  removePlugin: vi.fn(),
  getPluginSuggestions: vi.fn(async () => ({ name: "x", suggestions: [] })),
  getPluginMarketplaces: vi.fn(),
  getPluginMarketplaceEntries: vi.fn(),
  removePluginMarketplace: vi.fn(),
  installPlugin: vi.fn(),
  addPluginMarketplace: vi.fn(),
  uploadPlugin: vi.fn(),
  validatePluginFolder: vi.fn(),
}));

const CAPS = { text: 0, network: 0, process: 0, exec: 0 };

function plugin(over: Partial<Plugin> = {}): Plugin {
  return {
    name: "my-plugin",
    display_name: "",
    version: "1.0.0",
    description: "Everything a plugin can bring.",
    author: "Someone Else",
    homepage: "",
    keywords: [],
    source_kind: "github",
    source: "github:someone/my-plugin@a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0",
    ref: "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0",
    tree_sha256: "f".repeat(64),
    license: "MIT",
    installed_at: "",
    updated_at: "",
    status: "pending",
    denied: false,
    marketplace: "",
    from_anthropic: false,
    capabilities: { ...CAPS, text: 3, process: 1, exec: 1 },
    components: [
      { kind: "skill", name: "greet", path: "skills/greet/SKILL.md", capability: "text", detail: [], inert: false, reaches_agent: true },
      { kind: "command", name: "hello", path: "commands/hello.md", capability: "text", detail: [], inert: false, reaches_agent: true },
      { kind: "mcp_server", name: "files", path: ".mcp.json", capability: "process", detail: ["stdio"], inert: false, reaches_agent: false },
      { kind: "hook", name: "PreToolUse", path: "hooks/hooks.json", capability: "exec", detail: [], inert: true, reaches_agent: false },
    ],
    servers: [{ name: "files", configured_as: "plugin-my-plugin-files", added: false, capability: "process", detail: ["stdio"] }],
    held_diff: null,
    mcp_added: [],
    ...over,
  };
}

const NOTES = plugin({
  name: "notes",
  source: "/home/me/notes",
  ref: "",
  tree_sha256: "0123456789abcdef".repeat(4),
  capabilities: { ...CAPS, text: 2 },
  components: [
    { kind: "skill", name: "summarise", path: "skills/summarise/SKILL.md", capability: "text", detail: [], inert: false, reaches_agent: true },
  ],
  servers: [],
});

describe("Settings › Plugins", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(getPlugins).mockResolvedValue({ plugins: [], curated: false });
    vi.mocked(getPluginMarketplaces).mockResolvedValue({ marketplaces: [] });
  });

  it("is a Settings tab with an address", () => {
    expect(settingsTab("plugins")).toBe("plugins");
  });

  it("says honestly that nothing is installed and how to add one", async () => {
    renderWithProviders(<Plugins />);
    expect(await screen.findByText("No plugins installed.")).toBeInTheDocument();
    expect(screen.getByText(/upload a plugin folder or \.zip/)).toBeInTheDocument();
  });

  it("shows status, source, short pin and the capability line on each card", async () => {
    vi.mocked(getPlugins).mockResolvedValue({ plugins: [plugin(), NOTES], curated: false });
    renderWithProviders(<Plugins />);
    const card = await screen.findByRole("article", { name: "my-plugin" });
    expect(within(card).getByText("pending")).toBeInTheDocument();
    expect(within(card).getByText(/a1b2c3d$/)).toBeInTheDocument();
    expect(within(card).getByTestId("capability-line")).toHaveTextContent(
      "starts 1 MCP server(s) · brings hooks: inert",
    );
    const notes = screen.getByRole("article", { name: "notes" });
    expect(within(notes).getByTestId("capability-line")).toHaveTextContent("instructions only");
  });

  it("switching on asks first, lists what reaches the agent, and adds only the servers ticked", async () => {
    const user = userEvent.setup();
    vi.mocked(getPlugins).mockResolvedValue({ plugins: [plugin()], curated: false });
    vi.mocked(enablePlugin).mockResolvedValue(plugin({ status: "active" }));
    renderWithProviders(<Plugins />);
    await user.click(await screen.findByRole("switch", { name: "Switch my-plugin on or off" }));
    const dialog = await screen.findByRole("dialog", { name: "Switch on my-plugin?" });
    expect(enablePlugin).not.toHaveBeenCalled();
    expect(within(dialog).getByText("my-plugin:greet")).toBeInTheDocument();
    expect(within(dialog).getByText("my-plugin:hello")).toBeInTheDocument();
    const server = within(dialog).getByRole("checkbox");
    expect(server).not.toBeChecked();
    expect(within(dialog).getByText("Kept on disk and never run:")).toBeInTheDocument();
    await user.click(server);
    await user.click(within(dialog).getByRole("button", { name: "Switch on" }));
    await waitFor(() => expect(enablePlugin).toHaveBeenCalledWith("my-plugin", ["files"]));
  });

  it("confirming without ticking adds no server", async () => {
    const user = userEvent.setup();
    vi.mocked(getPlugins).mockResolvedValue({ plugins: [plugin()], curated: false });
    vi.mocked(enablePlugin).mockResolvedValue(plugin({ status: "active" }));
    renderWithProviders(<Plugins />);
    await user.click(await screen.findByRole("switch", { name: "Switch my-plugin on or off" }));
    await user.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Switch on" }));
    await waitFor(() => expect(enablePlugin).toHaveBeenCalledWith("my-plugin", []));
  });

  it("switching off needs no dialog", async () => {
    const user = userEvent.setup();
    vi.mocked(getPlugins).mockResolvedValue({ plugins: [plugin({ status: "active" })], curated: false });
    vi.mocked(disablePlugin).mockResolvedValue(plugin({ status: "inactive" }));
    renderWithProviders(<Plugins />);
    const on = await screen.findByRole("switch", { name: "Switch my-plugin on or off" });
    expect(on).toHaveAttribute("aria-checked", "true");
    await user.click(on);
    await waitFor(() => expect(disablePlugin).toHaveBeenCalledWith("my-plugin"));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("a held plugin shows the change it is held on before it can be switched on", async () => {
    vi.mocked(getPlugins).mockResolvedValue({
      plugins: [plugin({
        status: "held",
        held_diff: {
          files_added: ["hooks/hooks.json"], files_removed: [], files_changed: [],
          components_added: [{ kind: "hook", name: "Stop", capability: "exec" }],
          components_removed: [], components_changed: [], governed: true, reasons: ["file hooks/hooks.json"],
        },
      })],
      curated: false,
    });
    renderWithProviders(<Plugins />);
    expect(await screen.findByText(/Held: an update changed this plugin/)).toBeInTheDocument();
    expect(screen.getByText("+ hooks/hooks.json")).toBeInTheDocument();
    expect(screen.getByText(/Touches what runs or decides: file hooks\/hooks.json/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Review and switch on" })).toBeInTheDocument();
  });

  it("an update is checked first, its diff shown, and applied only on a second click", async () => {
    const user = userEvent.setup();
    vi.mocked(getPlugins).mockResolvedValue({ plugins: [NOTES], curated: false });
    vi.mocked(checkPluginUpdate).mockResolvedValue({
      name: "notes", changed: true, status: "pending", applied: false,
      diff: {
        files_added: [], files_removed: [], files_changed: ["commands/brief.md"],
        components_added: [], components_removed: [], components_changed: [], governed: false, reasons: [],
      },
    });
    vi.mocked(applyPluginUpdate).mockResolvedValue({ name: "notes", changed: true, status: "pending", applied: true, diff: null });
    renderWithProviders(<Plugins />);
    await user.click(await screen.findByRole("button", { name: "Check for update" }));
    expect(await screen.findByText("~ commands/brief.md")).toBeInTheDocument();
    expect(screen.getByText("Only text changed.")).toBeInTheDocument();
    expect(applyPluginUpdate).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "Apply (lands held)" }));
    await waitFor(() => expect(applyPluginUpdate).toHaveBeenCalledWith("notes"));
  });

  it("Discover says there is no curated catalogue and no marketplace yet", async () => {
    const user = userEvent.setup();
    renderWithProviders(<Plugins />);
    await user.click(await screen.findByRole("tab", { name: "Discover" }));
    expect(await screen.findByText(/does not ship a curated plugin catalogue/)).toBeInTheDocument();
    expect(screen.getByText(/No marketplace added/)).toBeInTheDocument();
  });

  it("Discover lists every entry: unpinned ones disabled with the reason, pinned ones install pending", async () => {
    const user = userEvent.setup();
    vi.mocked(getPluginMarketplaces).mockResolvedValue({
      marketplaces: [{ name: "team", kind: "local", repo: "", sha: "", path: "/m", description: "", owner: "", added_at: "", plugins: 3 }],
    });
    vi.mocked(getPluginMarketplaceEntries).mockResolvedValue({
      marketplace: "team",
      plugins: [
        { name: "notes", description: "Only words.", version: "", category: "productivity", tags: ["writing"],
          source: { kind: "local", repo: "", sha: "", url: "", path: "./plugins/notes" }, installable: true,
          refusal: "", installed: "", capabilities: { ...CAPS, text: 2 } },
        { name: "floating", description: "A server.", version: "", category: "dev", tags: [],
          source: { kind: "github", repo: "someone/floating", sha: "", url: "", path: "" }, installable: false,
          refusal: "the source is not pinned (github needs sha, an archive needs sha256)", installed: "", capabilities: null },
        { name: "pinned", description: "Pinned remote.", version: "", category: "dev", tags: [],
          source: { kind: "github", repo: "someone/pinned", sha: "a".repeat(40), url: "", path: "" }, installable: true,
          refusal: "", installed: "", capabilities: null },
      ],
    });
    vi.mocked(installPlugin).mockResolvedValue(NOTES);
    renderWithProviders(<Plugins />);
    await user.click(await screen.findByRole("tab", { name: "Discover" }));

    const floating = await screen.findByRole("article", { name: "floating" });
    expect(within(floating).getByText(/Not installable: the source is not pinned/)).toBeInTheDocument();
    expect(within(floating).getByRole("button", { name: "Install floating (switched off)" })).toBeDisabled();
    expect(within(screen.getByRole("article", { name: "pinned" })).getByTestId("capability-line")).toHaveTextContent(
      "known after installing",
    );

    await user.click(screen.getByRole("button", { name: "Install notes (switched off)" }));
    await waitFor(() => expect(installPlugin).toHaveBeenCalledWith({ plugin: "notes", marketplace: "team" }));

    await user.type(screen.getByRole("searchbox", { name: "Search plugins" }), "writing");
    expect(screen.getByRole("article", { name: "notes" })).toBeInTheDocument();
    expect(screen.queryByRole("article", { name: "floating" })).not.toBeInTheDocument();
    await user.clear(screen.getByRole("searchbox", { name: "Search plugins" }));

    await user.selectOptions(screen.getByRole("combobox", { name: "What it runs" }), "text");
    expect(screen.getByRole("article", { name: "notes" })).toBeInTheDocument();
    expect(screen.queryByRole("article", { name: "pinned" })).not.toBeInTheDocument();
    await user.selectOptions(screen.getByRole("combobox", { name: "What it runs" }), "");
    await user.selectOptions(screen.getByRole("combobox", { name: "Category" }), "dev");
    expect(screen.queryByRole("article", { name: "notes" })).not.toBeInTheDocument();
  });

  it("the sub-tabs are one tab stop that the arrow keys move through", async () => {
    const user = userEvent.setup();
    renderWithProviders(<Plugins />);
    const mine = await screen.findByRole("tab", { name: "Mine" });
    mine.focus();
    await user.keyboard("{ArrowRight}");
    expect(screen.getByRole("tab", { name: "Discover" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tab", { name: "Mine" })).toHaveAttribute("tabindex", "-1");
  });

  it("the Add menu offers marketplace, upload and create — create says there is no builder yet", async () => {
    const user = userEvent.setup();
    renderWithProviders(<Plugins />);
    await user.click(await screen.findByRole("button", { name: "Add" }));
    expect(await screen.findByRole("menuitem", { name: "Add marketplace" })).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: "Upload folder or .zip" })).toBeInTheDocument();
    await user.click(screen.getByRole("menuitem", { name: "Create plugin" }));
    const dialog = await screen.findByRole("dialog", { name: "Create plugin" });
    expect(within(dialog).getByText(/no plugin builder in the app yet/)).toBeInTheDocument();
  });
});
