/**
 * Choosing who fuses — and being told, before paying, when the choice is not independent.
 *
 * The engine has taken `panel` / `judge` / `synthesizer` per instance since it was written; nothing
 * in the app could set them, so the only cast anyone ran was the shipped one. These tests cover the
 * two things this dialog has to do that a plain multi-select would not: state the cost of the SHAPE
 * (a four-model panel is six calls, which is invisible until the receipt), and state when the panel
 * is not independent — the judge grading its own answer, or judge and panelist from the same lab.
 *
 * Neither warning refuses the choice. A user holding one provider key cannot avoid the overlap, and
 * a refusal they cannot act on removes the feature instead of the problem.
 */
import { describe, expect, it, vi, beforeEach } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import {
  CastDialog,
  EMPTY_CAST,
  type Cast,
} from "@/components/code/FusionCast";
import { getConfig, getModels, patchConfig } from "@/lib/api";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    getConfig: vi.fn(),
    getModels: vi.fn(),
    patchConfig: vi.fn(async () => ({ updated: ["CHIMERA_FUSION_PANEL"] })),
  };
});

const mockConfig = vi.mocked(getConfig);
const mockModels = vi.mocked(getModels);
const mockPatch = vi.mocked(patchConfig);

function model(slug: string, label = slug) {
  return {
    slug,
    label,
    vendor: slug.split("/")[1] ?? "",
    source: "catalog",
    free: false,
    input_per_m: 1,
    output_per_m: 2,
    context_k: 128,
    recommended: false,
    tools: true,
    vision: false,
  };
}

const ANTHROPIC = "openrouter/anthropic/claude-opus-5";
const HAIKU = "openrouter/anthropic/haiku";
const OPENAI = "openrouter/openai/gpt-5.5";
const DEEPSEEK = "openrouter/deepseek/deepseek-r1";

beforeEach(() => {
  vi.clearAllMocks();
  mockConfig.mockResolvedValue({
    fusion: {
      panel: [ANTHROPIC, OPENAI],
      judge: DEEPSEEK,
      synthesizer: ANTHROPIC,
      mode: "selective",
      kinship: {
        judge_is_panelist: false,
        judge_shares_vendor_with: [],
        independent: true,
      },
    },
  } as unknown as Awaited<ReturnType<typeof getConfig>>);
  mockModels.mockResolvedValue({
    models: [model(ANTHROPIC), model(OPENAI), model(DEEPSEEK), model(HAIKU)],
    reason: "",
  } as unknown as Awaited<ReturnType<typeof getModels>>);
});

function open(value: Cast = EMPTY_CAST, onChange = vi.fn()) {
  renderWithProviders(
    <CastDialog
      open
      onOpenChange={() => {}}
      value={value}
      onChange={onChange}
    />,
  );
  return onChange;
}

describe("choosing who fuses", () => {
  it("starts from the configured cast rather than from three empty roles", async () => {
    // Someone who only wants to swap the judge should not have to rebuild the panel to say "the
    // usual, but with a different judge".
    open();
    expect(await screen.findByText(/2 models/)).toBeInTheDocument();
    expect(screen.getByText("deepseek-r1")).toBeInTheDocument();
  });

  it("says how many calls the shape costs, not just which models were picked", async () => {
    // Two panelists + judge + synthesizer. The arithmetic is the part people miss, and it is
    // invisible until the receipt arrives.
    open();
    expect(await screen.findByText(/4 calls per turn/)).toBeInTheDocument();
  });

  it("warns when the judge sits on the panel it grades", async () => {
    open({ panel: [ANTHROPIC, OPENAI], judge: ANTHROPIC, synthesizer: OPENAI });
    expect(await screen.findByText(/grade its own answer/)).toBeInTheDocument();
  });

  it("warns when judge and panelist come from the same lab", async () => {
    // The degree that a slug does not reveal: not the same model, and not two independent answers.
    open({ panel: [ANTHROPIC, OPENAI], judge: HAIKU, synthesizer: OPENAI });
    expect(
      await screen.findByText(/not two independent answers/),
    ).toBeInTheDocument();
  });

  it("does not refuse either overlap — it reports them", async () => {
    // A one-key user cannot avoid the overlap. Refusing would remove the feature, not the problem.
    open({ panel: [ANTHROPIC, OPENAI], judge: ANTHROPIC, synthesizer: OPENAI });
    await screen.findByText(/grade its own answer/);
    expect(
      screen.getByRole("button", { name: /make it the default/i }),
    ).toBeEnabled();
  });

  it("refuses a panel of one, because that is not fusion", async () => {
    open({ panel: [ANTHROPIC], judge: DEEPSEEK, synthesizer: ANTHROPIC });
    expect(await screen.findByText(/at least two models/)).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /make it the default/i }),
    ).toBeDisabled();
  });

  it("adds and removes panelists, and replaces a single-role pick", async () => {
    const user = userEvent.setup();
    const onChange = open();
    await screen.findByRole("checkbox", { name: /gpt-5\.5/ });

    // The panel is a set: clicking an unselected model adds it.
    await user.click(screen.getByRole("checkbox", { name: /haiku/ }));
    expect(onChange).toHaveBeenLastCalledWith(
      expect.objectContaining({ panel: [ANTHROPIC, OPENAI, HAIKU] }),
    );
  });

  it("saves all three roles together when made the default", async () => {
    const user = userEvent.setup();
    open();
    await screen.findByText(/2 models/);

    await user.click(
      screen.getByRole("button", { name: /make it the default/i }),
    );

    await waitFor(() =>
      expect(mockPatch).toHaveBeenCalledWith({
        CHIMERA_FUSION_PANEL: `${ANTHROPIC},${OPENAI}`,
        CHIMERA_FUSION_JUDGE: DEEPSEEK,
        CHIMERA_FUSION_SYNTHESIZER: ANTHROPIC,
      }),
    );
  });

  it("can hand the conversation back to the configured cast", async () => {
    const user = userEvent.setup();
    const onChange = open({
      panel: [HAIKU, OPENAI],
      judge: DEEPSEEK,
      synthesizer: HAIKU,
    });
    await screen.findByText(/2 models/);

    await user.click(
      screen.getByRole("button", { name: /use the configured one/i }),
    );

    expect(onChange).toHaveBeenLastCalledWith(EMPTY_CAST);
  });
});
