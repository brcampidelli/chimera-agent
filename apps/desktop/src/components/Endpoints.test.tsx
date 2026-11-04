import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { NamedEndpoints } from "@/components/Endpoints";
import { addEndpoint, listEndpoints, removeEndpoint, testEndpoint } from "@/lib/api";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", () => ({
  listEndpoints: vi.fn(),
  addEndpoint: vi.fn(),
  removeEndpoint: vi.fn(),
  testEndpoint: vi.fn(),
}));

const ROW = { name: "vllm", host: "llm.example", key_configured: true };

/** What the server would hand back: the key is never part of any of these. */
function list(...endpoints: (typeof ROW)[]) {
  return { endpoints };
}

describe("NamedEndpoints — Settings › Connections", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(listEndpoints).mockResolvedValue(list(ROW));
  });

  it("lists each endpoint by name and host, and says whether its key variable is set", async () => {
    renderWithProviders(<NamedEndpoints />);
    expect(await screen.findByText("vllm")).toBeInTheDocument();
    expect(screen.getByText("llm.example")).toBeInTheDocument();
    expect(screen.getByText("Key set")).toBeInTheDocument();
  });

  it("never renders a key, and offers no password field anywhere on the card", async () => {
    renderWithProviders(<NamedEndpoints />);
    await screen.findByText("vllm");
    const card = document.getElementById("settings-card-endpoints");
    expect(card).not.toBeNull();
    expect(card!.textContent).not.toMatch(/sk-|api[_-]?key=/i);
    expect(card!.querySelectorAll('input[type="password"]')).toHaveLength(0);
  });

  it("adds an endpoint from the form, sending the key variable's NAME and never a key", async () => {
    const user = userEvent.setup();
    vi.mocked(listEndpoints)
      .mockResolvedValueOnce(list())
      .mockResolvedValue(list({ name: "lab", host: "lab.example", key_configured: false }));
    vi.mocked(addEndpoint).mockResolvedValue(list({ name: "lab", host: "lab.example", key_configured: false }));

    renderWithProviders(<NamedEndpoints />);
    await user.click(await screen.findByRole("button", { name: "Add endpoint" }));
    const save = screen.getByRole("button", { name: "Save" });
    expect(save).toBeDisabled();

    await user.type(screen.getByLabelText("Name"), "lab");
    await user.type(screen.getByLabelText(/Base URL/), "https://lab.example/v1");
    await user.type(screen.getByLabelText(/Key variable/), "LAB_API_KEY");
    expect(save).toBeEnabled();
    await user.click(save);

    await waitFor(() =>
      expect(addEndpoint).toHaveBeenCalledWith({ name: "lab", base_url: "https://lab.example/v1", key_env: "LAB_API_KEY" }),
    );
    expect(await screen.findByText("lab.example")).toBeInTheDocument();
    expect(screen.getByText("The key variable is not set")).toBeInTheDocument();
  });

  it("shows the server's reason when it refuses the endpoint, and keeps the form open", async () => {
    const user = userEvent.setup();
    vi.mocked(listEndpoints).mockResolvedValue(list());
    vi.mocked(addEndpoint).mockRejectedValue(
      new Error("Endpoint 'lab' base_url must be an absolute https URL or loopback http URL"),
    );

    renderWithProviders(<NamedEndpoints />);
    await user.click(await screen.findByRole("button", { name: "Add endpoint" }));
    await user.type(screen.getByLabelText("Name"), "lab");
    await user.type(screen.getByLabelText(/Base URL/), "http://lab.example/v1");
    await user.type(screen.getByLabelText(/Key variable/), "LAB_API_KEY");
    await user.click(screen.getByRole("button", { name: "Save" }));

    expect(await screen.findByText(/must be an absolute https URL/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save" })).toBeInTheDocument();
  });

  it("states the _API_KEY naming rule and the private-address rule before anything is saved", async () => {
    const user = userEvent.setup();
    vi.mocked(listEndpoints).mockResolvedValue(list());
    renderWithProviders(<NamedEndpoints />);
    await user.click(await screen.findByRole("button", { name: "Add endpoint" }));
    expect(screen.getByText(/must end in _API_KEY/)).toBeInTheDocument();
    expect(screen.getByText(/192\.168\.1\.20/)).toBeInTheDocument();
  });

  it("tests one endpoint and reports the model count", async () => {
    const user = userEvent.setup();
    vi.mocked(testEndpoint).mockResolvedValue({ name: "vllm", host: "llm.example", ok: true, models: 3, error: null });

    renderWithProviders(<NamedEndpoints />);
    await user.click(await screen.findByRole("button", { name: "Test" }));
    expect(testEndpoint).toHaveBeenCalledWith("vllm");
    expect(await screen.findByText("Reachable, 3 models")).toBeInTheDocument();
  });

  it("reports a failed test by its error class, not the request or the key", async () => {
    const user = userEvent.setup();
    vi.mocked(testEndpoint).mockResolvedValue({
      name: "vllm",
      host: "llm.example",
      ok: false,
      models: null,
      error: "Endpoint test failed (ConnectError)",
    });

    renderWithProviders(<NamedEndpoints />);
    await user.click(await screen.findByRole("button", { name: "Test" }));
    expect(await screen.findByText("Test failed: Endpoint test failed (ConnectError)")).toBeInTheDocument();
    expect(document.getElementById("settings-card-endpoints")!.textContent).not.toMatch(/sk-/);
  });

  it("removes an endpoint by name and refreshes the list", async () => {
    const user = userEvent.setup();
    vi.mocked(listEndpoints).mockResolvedValueOnce(list(ROW)).mockResolvedValue(list());
    vi.mocked(removeEndpoint).mockResolvedValue(list());

    renderWithProviders(<NamedEndpoints />);
    await user.click(await screen.findByRole("button", { name: "Remove vllm" }));
    await waitFor(() => expect(removeEndpoint).toHaveBeenCalledWith("vllm"));
    expect(await screen.findByText("No endpoints yet.")).toBeInTheDocument();
  });

  it("says when the list cannot be read instead of showing an empty state", async () => {
    vi.mocked(listEndpoints).mockRejectedValue(new Error("boom"));
    renderWithProviders(<NamedEndpoints />);
    const card = document.getElementById("settings-card-endpoints")!;
    expect(await within(card).findByText("Could not read the endpoints.")).toBeInTheDocument();
  });
});
