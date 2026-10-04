import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { OpenApiConnectors } from "@/components/OpenApiConnectors";
import type { Connector } from "@/lib/types";
import { renderWithProviders as render } from "@/test/utils";

/**
 * The OpenAPI connectors tab (study 29, P7.5).
 *
 * The server keeps three promises and the screen must not undercut any of them: a connector arrives
 * off, an operation that changes data cannot be ticked until the owner allows changes, and a key goes
 * in once and comes back as its last four characters at most.
 */
vi.mock("@/lib/api", () => ({
  getConnectors: vi.fn(),
  addConnector: vi.fn(),
  patchConnector: vi.fn(),
  setConnectorKey: vi.fn(),
  removeConnector: vi.fn(),
}));

const api = await import("@/lib/api");

const SECRET = "fake-key-fake-key-fake-key";

function connector(over: Partial<Connector> = {}): Connector {
  return {
    name: "pets",
    source: "https://api.pets.example/openapi.json",
    base_url: "https://api.pets.example",
    enabled: false,
    allow_writes: false,
    unattended: false,
    key_env: "CHIMERA_CONNECTOR_PETS_API_KEY",
    key_envs: ["CHIMERA_CONNECTOR_PETS_API_KEY", "BRAVE_API_KEY"],
    key_in: "header",
    key_name: "Authorization",
    key_prefix: "Bearer ",
    key_set: true,
    key_hint: "…6789",
    added_at: "2026-10-03T12:00:00+00:00",
    problem: "",
    operations: [
      { id: "listPets", tool: "api_pets_listPets", method: "GET", path: "/pets", summary: "List", selected: true },
      { id: "createPet", tool: "api_pets_createPet", method: "POST", path: "/pets", summary: "Create", selected: false },
    ],
    ...over,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.getConnectors).mockResolvedValue({
    connectors: [connector()],
    store: "/data/connectors.json",
  });
  vi.mocked(api.patchConnector).mockResolvedValue(connector());
  vi.mocked(api.setConnectorKey).mockResolvedValue(connector());
  vi.mocked(api.removeConnector).mockResolvedValue({ deleted: true });
  vi.mocked(api.addConnector).mockResolvedValue(connector());
});

describe("the OpenAPI connectors tab", () => {
  it("shows a connector off and locks an operation that changes data until changes are allowed", async () => {
    render(<OpenApiConnectors />);

    expect(await screen.findByText("off — pending")).toBeInTheDocument();
    expect(screen.getByRole("switch", { name: "Load pets's tools" })).toHaveAttribute("aria-checked", "false");
    const boxes = screen.getAllByRole("checkbox");
    expect(boxes[0]).toBeChecked();
    expect(boxes[1]).toBeDisabled();
    expect(screen.getByText("needs “allow changes”")).toBeInTheDocument();
    expect(screen.getByText("Reaches only https://api.pets.example")).toBeInTheDocument();
  });

  it("unlocks it once changes are allowed, and ticking sends the whole selection", async () => {
    vi.mocked(api.getConnectors).mockResolvedValue({
      connectors: [connector({ allow_writes: true })],
      store: "/data/connectors.json",
    });
    render(<OpenApiConnectors />);

    const post = (await screen.findAllByRole("checkbox"))[1];
    expect(post).not.toBeDisabled();
    await userEvent.click(post);

    expect(api.patchConnector).toHaveBeenCalledWith("pets", { operations: ["listPets", "createPet"] });
  });

  it("switches a connector on through the server, never by itself", async () => {
    render(<OpenApiConnectors />);

    await userEvent.click(await screen.findByRole("switch", { name: "Load pets's tools" }));

    expect(api.patchConnector).toHaveBeenCalledWith("pets", { enabled: true });
  });

  it("lets a broken connector be switched off, and only refuses switching it on", async () => {
    const problem = "the pinned copy of the spec is missing or unreadable — remove and add it again";
    vi.mocked(api.getConnectors).mockResolvedValue({
      connectors: [connector({ enabled: true, problem, operations: [] })],
      store: "/data/connectors.json",
    });
    const { unmount } = render(<OpenApiConnectors />);

    const on = await screen.findByRole("switch", { name: "Load pets's tools" });
    expect(on).not.toBeDisabled();
    await userEvent.click(on);
    expect(api.patchConnector).toHaveBeenCalledWith("pets", { enabled: false });
    unmount();

    vi.mocked(api.getConnectors).mockResolvedValue({
      connectors: [connector({ enabled: false, problem, operations: [] })],
      store: "/data/connectors.json",
    });
    render(<OpenApiConnectors />);
    expect(await screen.findByRole("switch", { name: "Load pets's tools" })).toBeDisabled();
  });

  it("keeps a connector to the app until the owner sends it to the bots and servers", async () => {
    render(<OpenApiConnectors />);

    const away = await screen.findByRole("switch", { name: "Also load it on the bots, scheduled jobs and servers" });
    expect(away).toHaveAttribute("aria-checked", "false");
    expect(screen.getByText(/except a bot, which gets nothing while any of your bots answers anyone/)).toBeInTheDocument();
    await userEvent.click(away);

    expect(api.patchConnector).toHaveBeenCalledWith("pets", { unattended: true });
  });

  it("shows the key as its last four characters and clears what was typed after saving", async () => {
    render(<OpenApiConnectors />);

    expect(await screen.findByText("set …6789")).toBeInTheDocument();
    const field = screen.getByLabelText("Paste the key; it is stored in .env and never shown again");
    expect(field).toHaveAttribute("type", "password");
    await userEvent.type(field, SECRET);
    await userEvent.click(screen.getByRole("button", { name: "Save key" }));

    expect(api.setConnectorKey).toHaveBeenCalledWith("pets", SECRET);
    await waitFor(() => expect(field).toHaveValue(""));
    expect(document.body.textContent).not.toContain(SECRET);
  });

  it("removes a connector only on the second press, which says the key stays", async () => {
    render(<OpenApiConnectors />);

    await userEvent.click(await screen.findByRole("button", { name: "Remove" }));
    expect(api.removeConnector).not.toHaveBeenCalled();
    const warning = screen.getByText("Remove pets and its pinned spec? Its key stays in .env.");
    const row = warning.parentElement as HTMLElement;
    await userEvent.click(within(row).getByRole("button", { name: "Remove" }));

    expect(api.removeConnector).toHaveBeenCalledWith("pets");
  });

  it("adds a connector from a URL and shows the server's refusal in its own words", async () => {
    vi.mocked(api.addConnector).mockRejectedValueOnce(new Error("blocked internal address 127.0.0.1"));
    render(<OpenApiConnectors />);

    await userEvent.type(await screen.findByLabelText("Name (lowercase, e.g. weather)"), " inner ");
    await userEvent.type(screen.getByLabelText("Spec URL or file path"), "http://127.0.0.1/spec");
    await userEvent.click(screen.getByRole("button", { name: "Add" }));

    expect(api.addConnector).toHaveBeenCalledWith({
      name: "inner",
      source: "http://127.0.0.1/spec",
      base_url: null,
    });
    expect(await screen.findByText("blocked internal address 127.0.0.1")).toBeInTheDocument();
  });
});
