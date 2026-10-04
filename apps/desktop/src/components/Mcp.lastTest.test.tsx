import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { Mcp } from "@/components/Mcp";
import { addMcpServer, getConfig, getMcpCatalog, getMcpServers, testMcpServer } from "@/lib/api";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", () => ({
  addMcpServer: vi.fn(),
  getConfig: vi.fn(),
  getMcpCatalog: vi.fn(),
  getMcpServers: vi.fn(),
  removeMcpServer: vi.fn(),
  testMcpServer: vi.fn(),
}));

/**
 * The last Test of each server is now remembered across a relaunch — and the badge that says
 * "connected" must still only come from a test made in THIS window. A stored "ok" from yesterday
 * says nothing about whether the server starts today; showing it green would be the claim the
 * screen exists to refuse.
 *
 * The other half: a catalogue pick can be saved and tested in one click, and that click is the
 * only thing that runs it — never the boot, never the plain Add, never a hand-written server.
 */

const NOTION = {
  id: "notion",
  label: "Notion",
  summary: "Search and read pages.",
  runner: "npx",
  available: true,
  command: "npx",
  args: ["-y", "mcp-remote@0.14.3", "https://mcp.notion.com/mcp"],
  env: {},
  secrets: [],
  containment: "It acts as you.",
  official: true,
  docs: "https://developers.notion.com/docs/get-started-with-mcp",
  auth: "oauth",
};

const DOCKER = {
  ...NOTION,
  id: "github",
  label: "GitHub",
  runner: "docker",
  command: "docker",
  args: ["run", "-i", "--rm", "ghcr.io/github/github-mcp-server"],
  docs: "https://github.com/github/github-mcp-server",
};

const TESTED = {
  name: "notion",
  command: "npx",
  args: ["-y"],
  env_keys: [],
  last_test: { ok: true, tool_count: 4, tested_at: 1_787_000_000 },
};

describe("the remembered MCP test", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(getMcpServers).mockResolvedValue({ servers: [TESTED], count: 1 } as never);
    vi.mocked(getConfig).mockResolvedValue({ mcp: { autoload: true } } as never);
    vi.mocked(getMcpCatalog).mockResolvedValue({ entries: [NOTION, DOCKER], count: 2 } as never);
    vi.mocked(addMcpServer).mockResolvedValue({ servers: [], count: 0 } as never);
    vi.mocked(testMcpServer).mockResolvedValue({
      ok: true,
      tools: [{ name: "search", description: "" }],
      error: null,
      reaches_agent: true,
      reaches_agent_reason: null,
    } as never);
  });

  it("shows a stored test as history, never as connected", async () => {
    renderWithProviders(<Mcp />);

    expect(await screen.findByText(/last tested .* · 4 tools/i)).toBeInTheDocument();
    expect(
      screen.queryByText(/connected · \d+ tools/i),
      "a test from an earlier session was shown as a live connection",
    ).toBeNull();
    expect(testMcpServer, "opening the screen ran a server").not.toHaveBeenCalled();
  });

  it("says a stored failure is a failure", async () => {
    vi.mocked(getMcpServers).mockResolvedValue({
      servers: [{ ...TESTED, last_test: { ok: false, tool_count: 0, tested_at: 1_787_000_000 } }],
      count: 1,
    } as never);
    renderWithProviders(<Mcp />);

    expect(await screen.findByText(/last test failed/i)).toBeInTheDocument();
    expect(screen.queryByText(/connected · \d+ tools/i)).toBeNull();
  });

  it("says connected only after a test made in this window", async () => {
    const user = userEvent.setup();
    renderWithProviders(<Mcp />);

    await user.click(await screen.findByRole("button", { name: /^test$/i }));

    expect(await screen.findByText(/connected · 1 tools/i)).toBeInTheDocument();
    // The live result replaces the history line rather than sitting beside it.
    expect(screen.queryByText(/last tested/i)).toBeNull();
  });
});

describe("add and test", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(getMcpServers).mockResolvedValue({ servers: [], count: 0 } as never);
    vi.mocked(getConfig).mockResolvedValue({ mcp: { autoload: true } } as never);
    vi.mocked(getMcpCatalog).mockResolvedValue({ entries: [NOTION, DOCKER], count: 2 } as never);
    vi.mocked(addMcpServer).mockResolvedValue({ servers: [], count: 0 } as never);
    vi.mocked(testMcpServer).mockResolvedValue({
      ok: true,
      tools: [{ name: "search", description: "" }],
      error: null,
    } as never);
  });

  it("tests the server the click saved, and only after saving it", async () => {
    const user = userEvent.setup();
    renderWithProviders(<Mcp />);
    const usar = await screen.findAllByRole("button", { name: /use this/i });
    await user.click(usar[0]);

    await user.click(await screen.findByRole("button", { name: /add and test/i }));

    await waitFor(() => expect(testMcpServer).toHaveBeenCalledWith("notion"));
    expect(addMcpServer).toHaveBeenCalledTimes(1);
    expect(vi.mocked(addMcpServer).mock.invocationCallOrder[0]).toBeLessThan(
      vi.mocked(testMcpServer).mock.invocationCallOrder[0],
    );
  });

  it("does not run anything on a plain Add", async () => {
    const user = userEvent.setup();
    renderWithProviders(<Mcp />);
    const usar = await screen.findAllByRole("button", { name: /use this/i });
    await user.click(usar[0]);

    await user.click(screen.getByRole("button", { name: /^add$/i }));

    await waitFor(() => expect(addMcpServer).toHaveBeenCalledTimes(1));
    expect(testMcpServer, "Add started the server without being asked").not.toHaveBeenCalled();
  });

  it("offers no add-and-test for a hand-written server", async () => {
    renderWithProviders(<Mcp />);

    await screen.findByText(/add a server/i);
    expect(screen.queryByRole("button", { name: /add and test/i })).toBeNull();
  });
});

describe("finding an entry", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(getMcpServers).mockResolvedValue({ servers: [], count: 0 } as never);
    vi.mocked(getConfig).mockResolvedValue({ mcp: { autoload: true } } as never);
    vi.mocked(getMcpCatalog).mockResolvedValue({ entries: [NOTION, DOCKER], count: 2 } as never);
  });

  it("filters by what the entry needs to run", async () => {
    const user = userEvent.setup();
    renderWithProviders(<Mcp />);
    await screen.findByText("Notion");

    await user.click(screen.getByRole("button", { name: "docker" }));

    expect(screen.getByRole("button", { name: "docker" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.queryByText("Notion")).toBeNull();
    expect(screen.getByText("GitHub")).toBeInTheDocument();
  });

  it("searches the machine facts, so a command finds its entry in any language", async () => {
    const user = userEvent.setup();
    renderWithProviders(<Mcp />);
    await screen.findByText("Notion");

    await user.type(screen.getByRole("textbox", { name: /search the catalogue/i }), "mcp.notion.com");

    expect(screen.getByText("Notion")).toBeInTheDocument();
    expect(screen.queryByText("GitHub")).toBeNull();

    await user.clear(screen.getByRole("textbox", { name: /search the catalogue/i }));
    await user.type(screen.getByRole("textbox", { name: /search the catalogue/i }), "nada-disso");
    expect(screen.getByText(/no entry matches/i)).toBeInTheDocument();
  });
});

const STRIPE = {
  ...NOTION,
  id: "stripe",
  label: "Stripe",
  args: [
    "-y",
    "mcp-remote@0.14.3",
    "https://mcp.stripe.com",
    "--header",
    "Authorization:${STRIPE_AUTH_HEADER}",
  ],
  secrets: [
    { key: "STRIPE_AUTH_HEADER", hint: "Bearer rk_...", source: "", pattern: "^Bearer \\S+$" },
  ],
  auth: "key",
  docs: "https://docs.stripe.com/mcp",
};

/**
 * Saving a key entry with the key empty is not a harmless half-step. The Stripe entry then sends an
 * empty Authorization header, Stripe answers 401, and the mcp-remote bridge answers that by opening
 * Stripe's OAuth consent page in the browser — the whole-user grant the key exists to avoid — and
 * keeping the tokens in its own file. So neither button saves until every declared secret is filled.
 */
describe("a catalogue entry that asks for a key", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(getMcpServers).mockResolvedValue({ servers: [], count: 0 } as never);
    vi.mocked(getConfig).mockResolvedValue({ mcp: { autoload: true } } as never);
    vi.mocked(getMcpCatalog).mockResolvedValue({ entries: [STRIPE], count: 1 } as never);
    vi.mocked(addMcpServer).mockResolvedValue({ servers: [], count: 0 } as never);
    vi.mocked(testMcpServer).mockResolvedValue({ ok: false, tools: [], error: "401" } as never);
  });

  it("cannot be added or tested until the key is pasted", async () => {
    const user = userEvent.setup();
    renderWithProviders(<Mcp />);
    await user.click(await screen.findByRole("button", { name: /use this/i }));

    expect(screen.getByRole("button", { name: /add and test/i })).toBeDisabled();
    expect(screen.getByRole("button", { name: /^add$/i })).toBeDisabled();
    expect(screen.getByText(/paste STRIPE_AUTH_HEADER before adding/i)).toBeInTheDocument();

    await user.type(screen.getByPlaceholderText("value"), "Bearer rk_test");
    expect(screen.queryByText(/paste STRIPE_AUTH_HEADER/i)).toBeNull();
    await user.click(screen.getByRole("button", { name: /add and test/i }));

    await waitFor(() => expect(testMcpServer).toHaveBeenCalledWith("stripe"));
    expect(vi.mocked(addMcpServer).mock.calls[0][0].env).toEqual({
      STRIPE_AUTH_HEADER: "Bearer rk_test",
    });
  });

  // "Not empty" was the whole guard, and the most natural paste passes it: the key alone, copied
  // from Stripe's dashboard, goes out as a header with no scheme and is refused like an empty one.
  it.each([
    ["the key alone", "rk_test_51Abc"],
    ["the word alone", "Bearer"],
    ["the word with a key broken by a space", "Bearer rk_test 51Abc"],
  ])("cannot be added or tested with %s", async (_label, valor) => {
    const user = userEvent.setup();
    renderWithProviders(<Mcp />);
    await user.click(await screen.findByRole("button", { name: /use this/i }));

    await user.type(screen.getByPlaceholderText("value"), valor);

    expect(screen.getByRole("button", { name: /add and test/i })).toBeDisabled();
    expect(screen.getByRole("button", { name: /^add$/i })).toBeDisabled();
    expect(screen.getByText(/STRIPE_AUTH_HEADER is not in the form/i)).toBeInTheDocument();
    expect(screen.queryByText(/paste STRIPE_AUTH_HEADER/i), "a filled value read as empty").toBeNull();
    expect(addMcpServer).not.toHaveBeenCalled();
  });

  it("checks the value that is saved, not any row that happens to be filled", async () => {
    // Two rows of the same key: Add sends the LAST one. A check of "some row has a value" passed on
    // the first and saved the empty second.
    const user = userEvent.setup();
    renderWithProviders(<Mcp />);
    await user.click(await screen.findByRole("button", { name: /use this/i }));
    await user.type(screen.getByPlaceholderText("value"), "Bearer rk_test");
    await user.click(screen.getByRole("button", { name: /add env var/i }));
    const chaves = screen.getAllByPlaceholderText("ENV_KEY");
    await user.type(chaves[chaves.length - 1], "STRIPE_AUTH_HEADER");

    expect(screen.getByRole("button", { name: /^add$/i })).toBeDisabled();
    expect(screen.getByText(/paste STRIPE_AUTH_HEADER before adding/i)).toBeInTheDocument();
  });

  it("says before the click that the test may open a browser sign-in", async () => {
    const user = userEvent.setup();
    renderWithProviders(<Mcp />);
    await user.click(await screen.findByRole("button", { name: /use this/i }));

    expect(screen.getByText(/may also open a sign-in page in your browser/i)).toBeInTheDocument();
  });
});

describe("saving a server again", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(getMcpServers).mockResolvedValue({ servers: [TESTED], count: 1 } as never);
    vi.mocked(getConfig).mockResolvedValue({ mcp: { autoload: true } } as never);
    vi.mocked(getMcpCatalog).mockResolvedValue({ entries: [NOTION], count: 1 } as never);
    vi.mocked(addMcpServer).mockResolvedValue({ servers: [TESTED], count: 1 } as never);
    vi.mocked(testMcpServer).mockResolvedValue({
      ok: true,
      tools: [{ name: "search", description: "" }],
      error: null,
      reaches_agent: true,
      reaches_agent_reason: null,
    } as never);
  });

  it("drops this window's connected badge for the server it replaced", async () => {
    const user = userEvent.setup();
    renderWithProviders(<Mcp />);
    await user.click(await screen.findByRole("button", { name: /^test$/i }));
    expect(await screen.findByText(/connected · 1 tools/i)).toBeInTheDocument();

    // The same name saved again — possibly a different token. The live result was about the old one.
    await user.click(screen.getByRole("button", { name: /use this/i }));
    await user.click(screen.getByRole("button", { name: /^add$/i }));

    await waitFor(() => expect(addMcpServer).toHaveBeenCalledTimes(1));
    await waitFor(() =>
      expect(
        screen.queryByText(/connected · \d+ tools/i),
        "a configuration that never connected is still shown as connected",
      ).toBeNull(),
    );
    expect(testMcpServer).toHaveBeenCalledTimes(1);
  });
});
