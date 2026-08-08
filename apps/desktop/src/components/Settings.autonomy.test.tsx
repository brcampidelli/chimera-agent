import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Settings } from "@/components/Settings";
import {
  getConfig,
  getDoctor,
  getInstructions,
  getMessaging,
  patchConfig,
} from "@/lib/api";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", () => ({
  getConfig: vi.fn(),
  getDoctor: vi.fn(),
  getInstructions: vi.fn(),
  getMessaging: vi.fn(),
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
  applies: {},
};

/**
 * "How much may my agent do" was three controls in three places, and none of them was on a screen:
 * the posture selector had been deleted, the Code screen hardcoded a pair, and CHIMERA_HOST_EXEC
 * was not even in the allowlist `PATCH /api/config` accepts.
 */
describe("Settings — how much the agent may do", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(getConfig).mockResolvedValue(CONFIG as never);
    vi.mocked(getDoctor).mockResolvedValue({
      has_any_key: true,
      configured_providers: ["openrouter"],
      default_model: "openrouter/x",
      tiers: { weak: "a", mid: "b", top: "c" },
      memory_backend: "json",
      cache: false,
      sandbox: "local",
    } as never);
    vi.mocked(getMessaging).mockResolvedValue({} as never);
    vi.mocked(getInstructions).mockResolvedValue({
      name: "",
      language: "",
      instructions: "",
    } as never);
  });

  const card = () => screen.getByRole("region", { name: "How much it may do" });

  it("saves a reach as a floor, and unset as genuinely unset", async () => {
    const user = userEvent.setup();
    renderWithProviders(<Settings />);
    await screen.findByRole("region", { name: "How much it may do" });

    await user.selectOptions(within(card()).getByRole("combobox", { name: "Reach" }), "read_only");
    await waitFor(() => expect(patchConfig).toHaveBeenCalledOnce());
    expect(vi.mocked(patchConfig).mock.calls[0][0]).toEqual({ CHIMERA_REACH: "read_only" });

    // "unset" is a real value, not a placeholder: empty means this deployment states no posture,
    // which is what every install has today and is NOT the same as stating a permissive one.
    await user.selectOptions(within(card()).getByRole("combobox", { name: "Reach" }), "unset");
    await waitFor(() => expect(vi.mocked(patchConfig).mock.calls).toHaveLength(2));
    expect(vi.mocked(patchConfig).mock.calls[1][0]).toEqual({ CHIMERA_REACH: "" });
  });

  it("does not hand over the machine on a dropdown selection", async () => {
    // `allow` is the only value here that takes the human out of the loop on the user's own machine,
    // including for cron jobs nobody is watching. Everything else applies straight away — asking to
    // confirm a narrowing would train people to click through the one confirmation that matters.
    const user = userEvent.setup();
    renderWithProviders(<Settings />);
    await screen.findByRole("region", { name: "How much it may do" });

    const select = within(card()).getByRole("combobox", { name: "Commands on this machine" });
    await user.selectOptions(select, "allow");

    expect(patchConfig).not.toHaveBeenCalled();
    await screen.findByText(/runs shell commands on this machine with nobody asked first/i);

    await user.click(screen.getByRole("button", { name: /I understand/i }));
    await waitFor(() => expect(patchConfig).toHaveBeenCalledOnce());
    expect(vi.mocked(patchConfig).mock.calls[0][0]).toEqual({ CHIMERA_HOST_EXEC: "allow" });
  });

  it("applies a narrowing without asking", async () => {
    const user = userEvent.setup();
    renderWithProviders(<Settings />);
    await screen.findByRole("region", { name: "How much it may do" });

    await user.selectOptions(
      within(card()).getByRole("combobox", { name: "Commands on this machine" }),
      "deny",
    );

    await waitFor(() => expect(patchConfig).toHaveBeenCalledOnce());
    expect(vi.mocked(patchConfig).mock.calls[0][0]).toEqual({ CHIMERA_HOST_EXEC: "deny" });
    expect(screen.queryByText(/nobody asked first/i)).not.toBeInTheDocument();
  });
});
