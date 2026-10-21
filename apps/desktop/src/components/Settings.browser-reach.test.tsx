import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Settings } from "@/components/Settings";
import { addBrowserTrustedSite, getBrowserLink, getBrowserTrustedSites, getConfig, getDoctor, getInstructions, getMessaging, patchConfig, removeBrowserTrustedSite, unpairBrowserLink } from "@/lib/api";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", () => ({
  getCompletionStats: vi.fn(async () => ({ accepted: 0, dismissed: 0, rate: null, mean_ms: null })),
  getConfig: vi.fn(),
  getDoctor: vi.fn(),
  getInstructions: vi.fn(),
  getMessaging: vi.fn(),
  getBrowserLink: vi.fn(async () => ({ paired: false, connected: false })),
  getBrowserTrustedSites: vi.fn(async () => ({ trusted_sites: [] })),
  stopBrowserLink: vi.fn(async () => ({ stopped: true })),
  unpairBrowserLink: vi.fn(async () => ({ paired: false })),
  addBrowserTrustedSite: vi.fn(async (_host: string) => ({ trusted_sites: [_host] })),
  removeBrowserTrustedSite: vi.fn(async (_host: string) => ({ trusted_sites: [] })),
  getOllamaModels: vi.fn(async () => ({
    base_url: "",
    reachable: false,
    models: [],
    reason: "no_url",
  })),
  patchConfig: vi.fn(async () => ({ updated: [] })),
  putInstructions: vi.fn(),
  startMessaging: vi.fn(),
  stopMessaging: vi.fn(),
}));

/**
 * Where the agent's browser may go (study 29, P5.2): a site list that only narrows, and loopback
 * ports the owner declares so the agent can look at the app it is changing. Both are switches the
 * owner decided must be changeable from the app, so the test that matters is that each row shows
 * what it holds and writes its own key — never the other one, and never a broader setting.
 */
function config(browser: Record<string, unknown> | undefined) {
  return {
    models: {
      default: "openrouter/x",
      weak: "",
      mid: "",
      orchestrator: "",
      cost_mode: "auto",
      cascade: false,
      api_base: null,
      fallback_models: [],
      tiers: { weak: "a", mid: "b", top: "c" },
    },
    memory: { backend: "json", semantic: false, auto_consolidate: false, remember_from_chat: false },
    cache: { completion: false, prompt: false },
    autonomy: { reach: "", approval: "", host_exec: "ask", denied_tools: [] },
    sandbox: { mode: "local", image: "python:3.12-slim" },
    server: { token_set: false },
    mcp: { autoload: false },
    automation: { cron: true },
    guard: { chat: false },
    providers: [],
    applies: {
      CHIMERA_BROWSER_SITES: "next_conversation",
      CHIMERA_BROWSER_LOCAL_PORTS: "next_conversation",
    },
    ...(browser === undefined ? {} : { browser }),
  };
}

/** What each PATCH sent; the mutation passes react-query's context as a second argument. */
const patched = () => vi.mocked(patchConfig).mock.calls.map((call) => call[0]);

describe("Settings — where the browser may go", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(getDoctor).mockResolvedValue({
      has_any_key: true,
      local_model: false,
      can_answer: true,
      configured_providers: ["openrouter"],
      default_model: "openrouter/x",
      tiers: { weak: "a", mid: "b", top: "c" },
      memory_backend: "json",
      cache: false,
      sandbox: "local",
    } as never);
    vi.mocked(getInstructions).mockResolvedValue({ text: "", path: "" } as never);
    vi.mocked(getMessaging).mockResolvedValue({ running: false, channels: [] } as never);
    vi.mocked(getBrowserLink).mockResolvedValue({ paired: false, connected: false });
    vi.mocked(getBrowserTrustedSites).mockResolvedValue({ trusted_sites: [] });
  });

  it("adds and removes exactly one trusted-site host", async () => {
    vi.mocked(getConfig).mockResolvedValue(config({ headless: true, sites: [], local_ports: [] }) as never);
    vi.mocked(getBrowserTrustedSites).mockResolvedValue({ trusted_sites: [] });
    const user = userEvent.setup();
    renderWithProviders(<Settings />);
    const entry = await screen.findByRole("textbox", { name: "Add trusted site" });
    await user.type(entry, "shop.example");
    await user.click(screen.getByRole("button", { name: "Add" }));
    await waitFor(() => expect(addBrowserTrustedSite).toHaveBeenCalledWith("shop.example"));
    expect(screen.getByRole("list", { name: "Always allow actions on these sites" })).toHaveTextContent("shop.example");
    await user.click(screen.getByRole("button", { name: "Remove shop.example" }));
    await waitFor(() => expect(removeBrowserTrustedSite).toHaveBeenCalledWith("shop.example"));
  });

  it("shows the browser link's disconnected, connected, and unpaired states", async () => {
    vi.mocked(getConfig).mockResolvedValue(config({ headless: true, sites: [], local_ports: [] }) as never);
    vi.mocked(getBrowserLink).mockResolvedValue({ paired: true, connected: false });
    const { rerender } = renderWithProviders(<Settings />);
    expect(await screen.findByText("Paired, disconnected")).toBeInTheDocument();
    vi.mocked(getBrowserLink).mockResolvedValue({ paired: true, connected: true });
    rerender(<Settings />);
    await waitFor(() => expect(screen.getByText("Connected")).toBeInTheDocument());
    vi.mocked(getBrowserLink).mockResolvedValue({ paired: false, connected: false });
    rerender(<Settings />);
    await waitFor(() => expect(screen.getByText("Not paired")).toBeInTheDocument());
  });

  it("asks before unpairing the Chrome extension", async () => {
    vi.mocked(getConfig).mockResolvedValue(config({ headless: true, sites: [], local_ports: [] }) as never);
    vi.mocked(getBrowserLink).mockResolvedValue({ paired: true, connected: false });
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    renderWithProviders(<Settings />);
    await userEvent.click(await screen.findByRole("button", { name: "Unpair" }));
    expect(confirm).toHaveBeenCalledWith(expect.stringContaining("Unpair?"));
    expect(unpairBrowserLink).not.toHaveBeenCalled();
  });

  it("shows the editable site list and local ports as written", async () => {
    vi.mocked(getConfig).mockResolvedValue(
      config({ headless: true, sites: ["github.com", "*.github.com"], local_ports: [3000, 5173] }) as never,
    );
    renderWithProviders(<Settings />);
    expect(await screen.findByRole("list", { name: "Sites the browser opens without asking" })).toHaveTextContent("github.com");
    expect(screen.getByRole("button", { name: "Remove github.com" })).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "Add allowed site" })).toHaveValue("");
    expect(screen.getByRole("textbox", { name: "Local ports the browser may open" })).toHaveValue(
      "3000, 5173",
    );
  });

  it("reads as empty when the server does not send the lists", async () => {
    // A server one release behind sends `browser: {headless}` only; empty is the shipped browser.
    vi.mocked(getConfig).mockResolvedValue(config({ headless: true }) as never);
    renderWithProviders(<Settings />);
    expect(await screen.findByText("No sites in the list. Any public site may be opened without asking.")).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "Local ports the browser may open" })).toHaveValue("");
  });

  it("says the browser is off when .env holds a value that does not parse", async () => {
    // The two rows read empty then — "any public site" — for a browser that is in no conversation.
    const reason = "CHIMERA_BROWSER_SITES: 'https://github.com' is not a host";
    vi.mocked(getConfig).mockResolvedValue(
      config({ headless: true, sites: [], local_ports: [], invalid: reason }) as never,
    );
    renderWithProviders(<Settings />);
    const alert = await screen.findByText(/the agent's browser is off in every conversation/);
    expect(alert).toHaveTextContent(reason);
    expect(alert).toHaveAttribute("role", "alert");
  });

  it("says nothing of the kind when both lists parse", async () => {
    vi.mocked(getConfig).mockResolvedValue(
      config({ headless: true, sites: ["github.com"], local_ports: [], invalid: null }) as never,
    );
    renderWithProviders(<Settings />);
    await screen.findByRole("list", { name: "Sites the browser opens without asking" });
    expect(screen.queryByText(/browser is off in every conversation/)).toBeNull();
  });

  it("adds and removes a site using isolated settings patches", async () => {
    vi.mocked(getConfig).mockResolvedValue(config({ headless: true, sites: ["github.com"], local_ports: [] }) as never);
    const user = userEvent.setup();
    renderWithProviders(<Settings />);
    const entry = await screen.findByRole("textbox", { name: "Add allowed site" });
    await user.type(entry, "*.example.com");
    await user.click(screen.getByRole("button", { name: "Add allowed site" }));
    await waitFor(() => expect(patched()).toContainEqual({ CHIMERA_BROWSER_SITES: "github.com, *.example.com" }));
    await user.click(screen.getByRole("button", { name: "Remove github.com" }));
    await waitFor(() => expect(patched()).toContainEqual({ CHIMERA_BROWSER_SITES: "" }));
  });

  it("rejects site syntax the backend would refuse before sending a PATCH", async () => {
    vi.mocked(getConfig).mockResolvedValue(config({ headless: true, sites: [], local_ports: [] }) as never);
    const user = userEvent.setup();
    renderWithProviders(<Settings />);
    const entry = await screen.findByRole("textbox", { name: "Add allowed site" });
    // Match `parse_sites`' refusal cases: the UI must not PATCH syntax the backend refuses.
    for (const invalid of [
      "https://example.com",
      "example.com/path",
      "example.com:443",
      "*",
      "*example.com",
      "a..b",
      "-x.com",
    ]) {
      await user.clear(entry);
      await user.type(entry, invalid);
      await user.click(screen.getByRole("button", { name: "Add allowed site" }));
      expect(await screen.findByRole("alert")).toHaveTextContent(
        "Enter a valid hostname, for example example.com or *.example.com.",
      );
    }
    expect(patchConfig).not.toHaveBeenCalled();
  });

  it("accepts an internationalized wildcard hostname as the backend does", async () => {
    vi.mocked(getConfig).mockResolvedValue(config({ headless: true, sites: [], local_ports: [] }) as never);
    const user = userEvent.setup();
    renderWithProviders(<Settings />);
    const entry = await screen.findByRole("textbox", { name: "Add allowed site" });
    await user.type(entry, "*.bücher.de");
    await user.click(screen.getByRole("button", { name: "Add allowed site" }));
    await waitFor(() => expect(patched()).toContainEqual({ CHIMERA_BROWSER_SITES: "*.bücher.de" }));
  });

  it("accepts a host with an s in it, which the first version of the check refused", async () => {
    vi.mocked(getConfig).mockResolvedValue(config({ headless: true, sites: [], local_ports: [] }) as never);
    const user = userEvent.setup();
    renderWithProviders(<Settings />);
    const entry = await screen.findByRole("textbox", { name: "Add allowed site" });
    await user.type(entry, "docs.github.com{Enter}");
    await waitFor(() => expect(patched()).toContainEqual({ CHIMERA_BROWSER_SITES: "docs.github.com" }));
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("says what the list does and what the cookie choices keep", async () => {
    vi.mocked(getConfig).mockResolvedValue(config({ headless: true, sites: [], local_ports: [] }) as never);
    renderWithProviders(<Settings />);
    expect(await screen.findByText(/\*\.example\.com does not include example\.com itself/)).toBeInTheDocument();
    expect(screen.getByText(/a yes holds for that host until the browser closes/)).toBeInTheDocument();
    expect(screen.getByText(/deleted with the conversation/)).toBeInTheDocument();
  });

  it("writes the selected cookie retention setting", async () => {
    vi.mocked(getConfig).mockResolvedValue(config({ headless: true, sites: [], local_ports: [], cookie_retention: "ephemeral" }) as never);
    const user = userEvent.setup();
    renderWithProviders(<Settings />);
    const selector = await screen.findByRole("combobox", { name: "Browser cookie retention" });
    await user.selectOptions(selector, "conversation");
    await waitFor(() => expect(patched()).toContainEqual({ CHIMERA_BROWSER_COOKIE_RETENTION: "conversation" }));
  });

  it("writes each list to its own key", async () => {
    vi.mocked(getConfig).mockResolvedValue(config({ headless: true, sites: [], local_ports: [] }) as never);
    const user = userEvent.setup();
    renderWithProviders(<Settings />);

    const ports = await screen.findByRole("textbox", { name: "Local ports the browser may open" });
    await user.type(ports, "3000");
    await user.click(within(ports.parentElement as HTMLElement).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(patchConfig).toHaveBeenCalledTimes(1));
    expect(vi.mocked(patchConfig).mock.calls[0][0]).toEqual({ CHIMERA_BROWSER_LOCAL_PORTS: "3000" });

    const sites = screen.getByRole("textbox", { name: "Add allowed site" });
    await user.type(sites, "github.com");
    await user.click(screen.getByRole("button", { name: "Add allowed site" }));
    await waitFor(() => expect(patchConfig).toHaveBeenCalledTimes(2));
    expect(vi.mocked(patchConfig).mock.calls[1][0]).toEqual({ CHIMERA_BROWSER_SITES: "github.com" });
  });
});
