/**
 * Settings › Plugins on the catalog shell (study 04, PRs 3 and 4): Mine grouped by who has to act,
 * Discover's "+" refusing with its reason in sight, and the detail panel driven by `item=` in the
 * URL — opened by the card, closed by Escape or the browser's Back, focus returned to the card, and
 * a link to an item that is gone saying so instead of drawing an empty panel.
 */
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { Plugins } from "@/components/plugins/Plugins";
import { enablePlugin, getPluginMarketplaceEntries, getPluginMarketplaces, getPlugins, installPlugin } from "@/lib/api";
import { renderWithProviders } from "@/test/utils";
import type { Plugin, PluginMarketplaceEntry } from "@/lib/types";

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

function plugin(name: string, over: Partial<Plugin> = {}): Plugin {
  return {
    name,
    display_name: "",
    version: "1.0.0",
    description: `The ${name} plugin.`,
    author: "Someone",
    homepage: "",
    keywords: [],
    source_kind: "local",
    source: `/plugins/${name}`,
    ref: "",
    tree_sha256: "0123456789abcdef".repeat(4),
    license: "MIT",
    installed_at: "",
    updated_at: "",
    status: "pending",
    denied: false,
    marketplace: "",
    from_anthropic: false,
    capabilities: { ...CAPS, text: 1 },
    components: [
      { kind: "skill", name: "s", path: "skills/s/SKILL.md", capability: "text", detail: [], inert: false, reaches_agent: true },
    ],
    servers: [],
    held_diff: null,
    mcp_added: [],
    ...over,
  };
}

function entry(name: string, over: Partial<PluginMarketplaceEntry> = {}): PluginMarketplaceEntry {
  return {
    name,
    description: `${name} from the team.`,
    version: "",
    category: "dev",
    tags: [],
    source: { kind: "local", repo: "", sha: "", url: "", path: `./plugins/${name}` },
    installable: true,
    refusal: "",
    installed: "",
    capabilities: { ...CAPS, text: 1 },
    ...over,
  };
}

const TEAM = { name: "team", kind: "local", repo: "", sha: "", path: "/m", description: "", owner: "", added_at: "", plugins: 2 };

function params(): URLSearchParams {
  return new URLSearchParams(window.location.hash.split("?")[1] ?? "");
}

describe("Settings › Plugins on the catalog shell", () => {
  beforeEach(() => {
    window.history.replaceState(null, "", "#/settings?tab=plugins");
    vi.clearAllMocks();
    vi.mocked(getPlugins).mockResolvedValue({ plugins: [], curated: false });
    vi.mocked(getPluginMarketplaces).mockResolvedValue({ marketplaces: [] });
  });

  it("groups Mine by who has to act: what waits for you first, then on, then off or denied", async () => {
    vi.mocked(getPlugins).mockResolvedValue({
      plugins: [
        plugin("alpha", { status: "active" }),
        plugin("bravo", { status: "inactive" }),
        plugin("charlie", { status: "pending" }),
        plugin("delta", { status: "held" }),
        plugin("echo", { status: "inactive", denied: true }),
      ],
      curated: false,
    });
    renderWithProviders(<Plugins />);
    await screen.findByRole("article", { name: "alpha" });

    const titles = screen.getAllByRole("heading", { level: 3 }).map((h) => h.textContent);
    expect(titles).toEqual(["Waiting for you", "On", "Off or denied"]);
    const names = (section: string) =>
      within(screen.getByRole("region", { name: section }))
        .getAllByRole("article")
        .map((a) => a.getAttribute("aria-label"));
    expect(names("Waiting for you")).toEqual(["charlie", "delta"]);
    expect(names("On")).toEqual(["alpha"]);
    expect(names("Off or denied")).toEqual(["bravo", "echo"]);

    // A plugin the policy denies has no switch to offer; the pill says why.
    const echo = screen.getByRole("article", { name: "echo" });
    expect(within(echo).getByText("Denied by policy")).toBeInTheDocument();
    expect(within(echo).queryByRole("switch")).not.toBeInTheDocument();
    expect(within(screen.getByRole("article", { name: "charlie" })).getByRole("switch")).toBeInTheDocument();
  });

  it("keeps an entry that cannot be installed listed, its + disabled and the reason visible on the card", async () => {
    const user = userEvent.setup();
    vi.mocked(getPluginMarketplaces).mockResolvedValue({ marketplaces: [TEAM] });
    vi.mocked(getPluginMarketplaceEntries).mockResolvedValue({
      marketplace: "team",
      plugins: [
        entry("floating", {
          installable: false,
          refusal: "the source is not pinned",
          capabilities: null,
          source: { kind: "github", repo: "someone/floating", sha: "", url: "", path: "" },
        }),
      ],
    });
    renderWithProviders(<Plugins />);
    await user.click(await screen.findByRole("tab", { name: "Discover" }));

    const card = await screen.findByRole("article", { name: "floating" });
    const reason = within(card).getByText("Not installable: the source is not pinned");
    expect(reason).toBeVisible();
    const plus = within(card).getByRole("button", { name: "Install floating (switched off)" });
    expect(plus).toBeDisabled();
    // The reason is what describes the disabled button, not only text beside it.
    expect(plus.getAttribute("aria-describedby")).toBe(reason.id);

    // The detail's Install step refuses the same way, with the same reason.
    await user.click(within(card).getByRole("button", { name: "floating" }));
    const panel = await screen.findByRole("dialog", { name: "floating" });
    const install = within(panel).getByRole("button", { name: "Install floating (switched off)" });
    expect(install).toBeDisabled();
    expect(within(panel).getByText("Not installable: the source is not pinned")).toBeVisible();
    expect(installPlugin).not.toHaveBeenCalled();
  });

  it("opens the detail from the card, puts it in the URL, and Escape returns focus to the card", async () => {
    const user = userEvent.setup();
    vi.mocked(getPlugins).mockResolvedValue({ plugins: [plugin("notes")], curated: false });
    renderWithProviders(<Plugins />);
    const title = await screen.findByRole("button", { name: "notes" });
    await user.click(title);

    const panel = await screen.findByRole("dialog", { name: "notes" });
    expect(params().get("item")).toBe("plugin:notes");
    expect(params().get("tab")).toBe("plugins");
    // The old card body is the panel's body, and the steps sit at its foot.
    expect(within(panel).getByText("The notes plugin.")).toBeInTheDocument();
    const steps = within(panel).getByRole("list", { name: "Steps" });
    expect(within(steps).getByText("Install switched off")).toBeInTheDocument();
    expect(within(steps).getByRole("button", { name: "Review and switch on" })).toBeInTheDocument();

    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(params().has("item")).toBe(false);
    await waitFor(() => expect(title).toHaveFocus());
  });

  it("the browser's Back closes the panel it opened", async () => {
    const user = userEvent.setup();
    vi.mocked(getPlugins).mockResolvedValue({ plugins: [plugin("notes")], curated: false });
    renderWithProviders(<Plugins />);
    await user.click(await screen.findByRole("button", { name: "notes" }));
    await screen.findByRole("dialog", { name: "notes" });
    window.history.back();
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(params().has("item")).toBe(false);
  });

  it("a pasted link opens the panel, and its review step asks before switching on", async () => {
    const user = userEvent.setup();
    window.history.replaceState(null, "", "#/settings?tab=plugins&item=plugin%3Anotes");
    vi.mocked(getPlugins).mockResolvedValue({ plugins: [plugin("notes")], curated: false });
    vi.mocked(enablePlugin).mockResolvedValue(plugin("notes", { status: "active" }));
    renderWithProviders(<Plugins />);
    const panel = await screen.findByRole("dialog", { name: "notes" });
    await user.click(within(panel).getByRole("button", { name: "Review and switch on" }));
    const review = await screen.findByRole("dialog", { name: "Switch on notes?" });
    expect(enablePlugin).not.toHaveBeenCalled();
    await user.click(within(review).getByRole("button", { name: "Switch on" }));
    await waitFor(() => expect(enablePlugin).toHaveBeenCalledWith("notes", []));
  });

  it("a link to an entry that is not installed offers Install as the first step", async () => {
    const user = userEvent.setup();
    window.history.replaceState(null, "", "#/settings?tab=plugins&view=discover&item=plugin%3Anotes%40team");
    vi.mocked(getPluginMarketplaces).mockResolvedValue({ marketplaces: [TEAM] });
    vi.mocked(getPluginMarketplaceEntries).mockResolvedValue({ marketplace: "team", plugins: [entry("notes")] });
    vi.mocked(installPlugin).mockResolvedValue(plugin("notes"));
    renderWithProviders(<Plugins />);
    const panel = await screen.findByRole("dialog", { name: "notes" });
    expect(within(panel).getByText("Text only")).toBeInTheDocument();
    await user.click(within(panel).getByRole("button", { name: "Install notes (switched off)" }));
    await waitFor(() => expect(installPlugin).toHaveBeenCalledWith({ plugin: "notes", marketplace: "team" }));
  });

  it("a link to an item that is gone says so instead of opening an empty panel", async () => {
    const user = userEvent.setup();
    window.history.replaceState(null, "", "#/settings?tab=plugins&item=plugin%3Agone");
    vi.mocked(getPlugins).mockResolvedValue({ plugins: [plugin("notes")], curated: false });
    renderWithProviders(<Plugins />);
    expect(await screen.findByText("This item is no longer in the catalog.")).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.getByRole("article", { name: "notes" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Close" }));
    expect(params().has("item")).toBe(false);
    expect(screen.queryByText("This item is no longer in the catalog.")).not.toBeInTheDocument();
  });
});
