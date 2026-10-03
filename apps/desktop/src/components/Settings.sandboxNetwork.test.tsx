import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Settings } from "@/components/Settings";
import {
  getConfig,
  getDoctor,
  getInstructions,
  getMessaging,
  getSandboxState,
  patchConfig,
} from "@/lib/api";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", () => ({
  getCompletionStats: vi.fn(async () => ({ accepted: 0, dismissed: 0, rate: null, mean_ms: null })),
  getConfig: vi.fn(),
  getDoctor: vi.fn(),
  getInstructions: vi.fn(),
  getMessaging: vi.fn(),
  getOllamaModels: vi.fn(async () => ({
    base_url: "",
    reachable: false,
    models: [],
    reason: "no_url",
  })),
  getSandboxState: vi.fn(),
  patchConfig: vi.fn(async () => ({ updated: [] })),
  putInstructions: vi.fn(),
  startMessaging: vi.fn(),
  stopMessaging: vi.fn(),
}));

const CONFIG = {
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
    ollama_base_url: "http://localhost:11434",
    lm_studio_base_url: "http://localhost:1234/v1",
    complete_model: "",
    voice_model: "",
    voice_work_model: "",
  },
  memory: { backend: "json", semantic: false, auto_consolidate: false, remember_from_chat: false },
  cache: { completion: false, prompt: false },
  autonomy: { reach: "read_only", approval: "", host_exec: "ask", denied_tools: [] },
  sandbox: { mode: "auto", image: "python:3.12-slim", network: "none", verify_network: false },
  browser: { headless: true },
  server: { token_set: false },
  mcp: { autoload: false },
  automation: { cron: true },
  guard: { chat: false },
  providers: [],
  // Mirrors `config_api.APPLIES_WHEN`: commands take a save now, an open chat at its next start.
  applies: { CHIMERA_SANDBOX_NETWORK: "commands_now" },
  pinned: [],
};

const HOST = {
  configured: "auto",
  backend: "host",
  isolated: false,
  reason: "Windows has no OS sandbox in Chimera.",
  reason_code: "windows",
  platform: "Windows",
  network: "host",
};

function sandbox(mode: string, network = "none", verifyNetwork = false) {
  return {
    ...CONFIG,
    sandbox: { mode, image: "python:3.12-slim", network, verify_network: verifyNetwork },
  };
}

const CONTAINER = {
  ...HOST,
  configured: "docker",
  backend: "docker",
  isolated: true,
  reason: "",
  reason_code: "",
  network: "none",
};

const KERNEL = {
  configured: "auto",
  backend: "bubblewrap",
  isolated: true,
  reason: "",
  reason_code: "",
  platform: "Linux",
  network: "none",
};

function doctor(codePython: Record<string, unknown> | undefined) {
  return {
    has_any_key: true,
    local_model: false,
    can_answer: true,
    configured_providers: ["openrouter"],
    default_model: "openrouter/x",
    tiers: { weak: "a", mid: "b", top: "c" },
    memory_backend: "json",
    cache: false,
    sandbox: "auto",
    code_python: codePython,
  };
}

const INTERPRETER = {
  path: "C:\\Python312\\python.exe",
  source: "interpreter",
  frozen: false,
  looked_for: ["python", "py"],
};

async function card() {
  // The Sandbox select is the card's first sandbox control and is always there; waiting for it
  // means the config has rendered, so an ABSENT row below is absent and not merely not yet loaded.
  await screen.findByRole("combobox", { name: "Sandbox" });
  return screen.getByRole("region", { name: "Cache & sandbox" });
}

/**
 * The docker sandbox's network, exposed — and only where it is a fence (study 29, P5.4).
 *
 * `CHIMERA_SANDBOX_NETWORK` was read by the factory and reachable only through `.env`. Offering it
 * as a plain "none / bridge" row would have been worse than its absence: on Windows the default
 * resolves to this machine, whose network nothing fences, and a row reading "none" there is a
 * boundary that does not exist. So the switch shows only when the sandbox that would run a command
 * is a container that answered, and every other case says what is true in words.
 */
describe("Settings — the network a command can reach", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(getConfig).mockResolvedValue(CONFIG as never);
    vi.mocked(getDoctor).mockResolvedValue(doctor(INTERPRETER) as never);
    vi.mocked(getSandboxState).mockResolvedValue(HOST as never);
    vi.mocked(getMessaging).mockResolvedValue({} as never);
    vi.mocked(getInstructions).mockResolvedValue(
      { name: "", language: "", instructions: "" } as never,
    );
  });

  it("hides the switch on the host and says the network is not isolated", async () => {
    renderWithProviders(<Settings />);
    const region = await card();

    expect(await within(region).findByText("not isolated")).toBeInTheDocument();
    expect(within(region).getByText(/nothing here fences it/)).toBeInTheDocument();
    expect(
      within(region).queryByRole("combobox", { name: "Command network" }),
    ).not.toBeInTheDocument();
  });

  it("hides the switch when docker was chosen and did not answer, and says why", async () => {
    vi.mocked(getConfig).mockResolvedValue(sandbox("docker") as never);
    vi.mocked(getSandboxState).mockResolvedValue({
      ...HOST,
      configured: "docker",
      reason_code: "no_container",
    } as never);
    renderWithProviders(<Settings />);
    const region = await card();

    expect(await within(region).findByText(/Docker is not answering/)).toBeInTheDocument();
    expect(
      within(region).queryByRole("combobox", { name: "Command network" }),
    ).not.toBeInTheDocument();
  });

  it("says a kernel sandbox blocks the network, without offering a switch that does not reach it", async () => {
    vi.mocked(getSandboxState).mockResolvedValue({
      configured: "auto",
      backend: "bubblewrap",
      isolated: true,
      reason: "",
      reason_code: "",
      platform: "Linux",
      network: "none",
    } as never);
    renderWithProviders(<Settings />);
    const region = await card();

    expect(await within(region).findByText("blocked")).toBeInTheDocument();
    expect(within(region).getByText(/The container setting does not apply here/)).toBeInTheDocument();
    expect(
      within(region).queryByRole("combobox", { name: "Command network" }),
    ).not.toBeInTheDocument();
  });

  it("reads an older server's missing field as the host, never as closed", async () => {
    const { network: _network, ...old } = HOST;
    vi.mocked(getSandboxState).mockResolvedValue(old as never);
    renderWithProviders(<Settings />);
    const region = await card();

    expect(await within(region).findByText("not isolated")).toBeInTheDocument();
    expect(within(region).queryByText("blocked")).not.toBeInTheDocument();
  });

  it("offers the switch inside a container that answered, closed by default", async () => {
    vi.mocked(getConfig).mockResolvedValue(sandbox("docker") as never);
    vi.mocked(getSandboxState).mockResolvedValue({
      ...HOST,
      configured: "docker",
      backend: "docker",
      isolated: true,
      reason: "",
      reason_code: "",
      network: "none",
    } as never);
    const user = userEvent.setup();
    renderWithProviders(<Settings />);
    const region = await card();

    const select = await within(region).findByRole("combobox", { name: "Command network" });
    expect(select).toHaveValue("none");
    expect(
      within(region).getByText(/Shell commands and execute_code in the container have no network/),
    ).toBeInTheDocument();
    // When it applies is said on the row, and both moments are said: a `!` command, a workflow and
    // the verifier take a save at once; only an open chat keeps the network it started with. "Next
    // conversation" alone described the side that widens access as later than it is.
    const note = within(region).getByText(/new commands, workflows and the verifier use it now/);
    expect(note).toHaveTextContent(/an open conversation keeps the network it started with/);
    expect(within(region).queryByText(/applies to your next conversation/)).not.toBeInTheDocument();

    await user.selectOptions(select, "bridge");
    await waitFor(() => expect(patchConfig).toHaveBeenCalledOnce());
    expect(vi.mocked(patchConfig).mock.calls[0][0]).toEqual({ CHIMERA_SANDBOX_NETWORK: "bridge" });
  });

  it("warns, on the row, what bridge opens", async () => {
    vi.mocked(getConfig).mockResolvedValue(sandbox("docker", "bridge") as never);
    vi.mocked(getSandboxState).mockResolvedValue({
      ...HOST,
      configured: "docker",
      backend: "docker",
      isolated: true,
      reason: "",
      reason_code: "",
      network: "bridge",
    } as never);
    renderWithProviders(<Settings />);
    const region = await card();

    expect(await within(region).findByRole("combobox", { name: "Command network" })).toHaveValue(
      "bridge",
    );
    const warning = within(region).getByText(/Not an allowlist/);
    expect(warning).toHaveClass("text-warn-foreground");
  });

  it("shows the configured sandbox as itself, auto included", async () => {
    // `auto` is the default and was not among the options, so the select showed "local".
    renderWithProviders(<Settings />);

    const select = await screen.findByRole("combobox", { name: "Sandbox" });
    expect(select).toHaveValue("auto");
    expect(within(select).getByRole("option", { name: "auto" })).toBeInTheDocument();
  });

  it("names the Python execute_code runs on this machine", async () => {
    renderWithProviders(<Settings />);
    const region = await card();

    expect(await within(region).findByText(INTERPRETER.path)).toBeInTheDocument();
    expect(within(region).getByText("The Python running Chimera.")).toBeInTheDocument();
  });

  it("says a frozen build found no Python, and what it looked for", async () => {
    vi.mocked(getDoctor).mockResolvedValue(
      doctor({ path: "", source: "missing", frozen: true, looked_for: ["python", "py"] }) as never,
    );
    renderWithProviders(<Settings />);
    const region = await card();

    expect(await within(region).findByText("none found")).toBeInTheDocument();
    expect(within(region).getByText(/Looked for python, py on PATH/)).toBeInTheDocument();
  });

  it("says the container's own Python answers inside a container", async () => {
    vi.mocked(getConfig).mockResolvedValue(sandbox("docker") as never);
    vi.mocked(getSandboxState).mockResolvedValue({
      ...HOST,
      configured: "docker",
      backend: "docker",
      isolated: true,
      reason: "",
      reason_code: "",
      network: "none",
    } as never);
    renderWithProviders(<Settings />);
    const region = await card();

    expect(await within(region).findByText("the image's Python")).toBeInTheDocument();
    expect(within(region).queryByText(INTERPRETER.path)).not.toBeInTheDocument();
  });
  it("never keeps the sandbox probe cached, so the Security screen cannot be served a stale one", async () => {
    // The app's own defaults (`main.tsx`): fresh for 30 s, kept 5 min. Security reads the same key
    // with `staleTime: 0, gcTime: 0` so a Docker daemon that died since the last look changes the
    // answer; TanStack keeps the LONGEST gcTime any observer asked for, so one observer here with the
    // defaults kept the old "isolated / container" around and Security showed it while re-probing.
    const qc = new QueryClient({
      defaultOptions: { queries: { retry: false, staleTime: 30_000, refetchOnWindowFocus: false } },
    });
    const app = (
      <QueryClientProvider client={qc}>
        <Settings />
      </QueryClientProvider>
    );
    const first = renderWithProviders(app);
    await card();
    await waitFor(() => expect(getSandboxState).toHaveBeenCalledTimes(1));

    first.unmount();
    await waitFor(() =>
      expect(qc.getQueryCache().find({ queryKey: ["governance-sandbox"] })).toBeUndefined(),
    );

    // And coming back within the 30 s the rest of the app treats as fresh probes again.
    renderWithProviders(app);
    await card();
    await waitFor(() => expect(getSandboxState).toHaveBeenCalledTimes(2));
  });
  // --- each sentence claims only what the code does ---------------------------------------------

  it("says the verifier is the exception to a closed container network, when it is on", async () => {
    vi.mocked(getConfig).mockResolvedValue(sandbox("docker", "none", true) as never);
    vi.mocked(getSandboxState).mockResolvedValue(CONTAINER as never);
    renderWithProviders(<Settings />);
    const region = await card();

    const hint = await within(region).findByText(/have no network/);
    // core/verify.py rebuilds the container with network=True for the verify command.
    expect(hint).toHaveTextContent(/Exception: CHIMERA_VERIFY_NETWORK is on/);
    expect(hint).toHaveTextContent(/container with the network open/);
  });

  it("says a typed verify command leaves a blocking kernel sandbox for this machine, when it is on", async () => {
    vi.mocked(getConfig).mockResolvedValue(sandbox("auto", "none", true) as never);
    vi.mocked(getSandboxState).mockResolvedValue(KERNEL as never);
    renderWithProviders(<Settings />);
    const region = await card();

    expect(await within(region).findByText("blocked")).toBeInTheDocument();
    expect(within(region).getByText(/gives shell commands and execute_code no network/)).toHaveTextContent(
      /a verify command you typed runs on this machine/,
    );
  });

  it("names no exception while CHIMERA_VERIFY_NETWORK is off", async () => {
    vi.mocked(getConfig).mockResolvedValue(sandbox("docker") as never);
    vi.mocked(getSandboxState).mockResolvedValue(CONTAINER as never);
    renderWithProviders(<Settings />);
    const region = await card();

    await within(region).findByRole("combobox", { name: "Command network" });
    expect(within(region).queryByText(/CHIMERA_VERIFY_NETWORK/)).not.toBeInTheDocument();
  });

  it("says bridge also reaches services on this machine", async () => {
    vi.mocked(getConfig).mockResolvedValue(sandbox("docker", "bridge") as never);
    vi.mocked(getSandboxState).mockResolvedValue({ ...CONTAINER, network: "bridge" } as never);
    renderWithProviders(<Settings />);
    const region = await card();

    // Docker Desktop's host.docker.internal reaches this machine's loopback: Ollama, our own API.
    expect(await within(region).findByText(/Not an allowlist/)).toHaveTextContent(
      /services listening on this machine/,
    );
  });

  it("says execute_code cannot run without a Python, not that no code can", async () => {
    vi.mocked(getDoctor).mockResolvedValue(
      doctor({ path: "", source: "missing", frozen: true, looked_for: ["python", "py"] }) as never,
    );
    renderWithProviders(<Settings />);
    const region = await card();

    // code_interpreter runs in-process, inside the app's own interpreter, Python on PATH or not.
    const hint = await within(region).findByText(/Looked for python, py on PATH/);
    expect(hint).toHaveTextContent(/execute_code cannot run here/);
    expect(hint).toHaveTextContent(/code_interpreter still runs/);
    expect(hint).not.toHaveTextContent(/code the agent writes/);
  });

  it("says the container's Python is the image's, naming the image", async () => {
    vi.mocked(getConfig).mockResolvedValue(sandbox("docker") as never);
    vi.mocked(getSandboxState).mockResolvedValue(CONTAINER as never);
    renderWithProviders(<Settings />);
    const region = await card();

    expect(await within(region).findByText("the image's Python")).toBeInTheDocument();
    expect(within(region).getByText(/python3 or python the image python:3\.12-slim has/)).toBeInTheDocument();
    expect(within(region).queryByText(/python3 in the container/)).not.toBeInTheDocument();
  });

  it("names this machine's Python while the sandbox answer is missing, outside docker", async () => {
    // Our own endpoint failing is not evidence about the machine; nor does it hide the Python row,
    // which depends only on doctor when nothing can put the command in a container.
    vi.mocked(getSandboxState).mockRejectedValue(new Error("boom") as never);
    renderWithProviders(<Settings />);
    const region = await card();

    expect(await within(region).findByText(INTERPRETER.path)).toBeInTheDocument();
    expect(within(region).queryByText("not isolated")).not.toBeInTheDocument();
  });

  it("says nothing about the Python while it cannot tell whether docker answered", async () => {
    vi.mocked(getConfig).mockResolvedValue(sandbox("docker") as never);
    vi.mocked(getSandboxState).mockRejectedValue(new Error("boom") as never);
    renderWithProviders(<Settings />);
    const region = await card();

    await waitFor(() => expect(getSandboxState).toHaveBeenCalled());
    expect(within(region).queryByText(INTERPRETER.path)).not.toBeInTheDocument();
    expect(within(region).queryByText("the image's Python")).not.toBeInTheDocument();
  });
});
