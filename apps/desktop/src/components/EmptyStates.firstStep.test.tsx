import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { Decisions } from "@/components/Decisions";
import { Memory } from "@/components/Memory";
import { Runs } from "@/components/Runs";
import { AppliesNote, AppliesContext, Row } from "@/components/settings/rows";
import { MineHeader } from "@/components/skills/SkillsHeaders";
import { EmptyState } from "@/components/ui/panel";
import { TooltipProvider } from "@/components/ui/tooltip";
import { DICTS, I18nProvider } from "@/lib/i18n";

const getMemory = vi.fn();
const getMemoryLayers = vi.fn();
const getRuns = vi.fn();
const getPausedRuns = vi.fn();
const getDecisions = vi.fn();

vi.mock("@/lib/api", () => ({
  getMemory: (...a: unknown[]) => getMemory(...a),
  getMemoryLayers: (...a: unknown[]) => getMemoryLayers(...a),
  addMemory: vi.fn(),
  deleteMemory: vi.fn(),
  getRuns: (...a: unknown[]) => getRuns(...a),
  getPausedRuns: (...a: unknown[]) => getPausedRuns(...a),
  getDecisions: (...a: unknown[]) => getDecisions(...a),
  labelDecision: vi.fn(),
  getEffectiveSkills: vi.fn(() => new Promise(() => undefined)),
}));

const en = DICTS.en;

function wrap(node: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <I18nProvider>
      <QueryClientProvider client={qc}>
        <TooltipProvider>{node}</TooltipProvider>
      </QueryClientProvider>
    </I18nProvider>,
  );
}

afterEach(() => {
  vi.clearAllMocks();
  window.location.hash = "";
});

describe("EmptyState", () => {
  it("still renders the old shape, text only", () => {
    render(<EmptyState text="Nothing here" />);
    expect(screen.getByText("Nothing here")).toBeInTheDocument();
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("takes a title, a hint and an action", () => {
    render(<EmptyState title="T" text="body" hint="why" action={<button type="button">go</button>} />);
    expect(screen.getByText("T")).toBeInTheDocument();
    expect(screen.getByText("why")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "go" })).toBeInTheDocument();
  });
});

describe("the first step an empty screen offers", () => {
  it("Memory: adding the first fact moves the focus to the field", async () => {
    getMemory.mockResolvedValue([]);
    getMemoryLayers.mockResolvedValue({ total: 0, clean: 0, tainted: 0, layers: [] });
    wrap(<Memory />);
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: en["memory.empty.action"] }));
    expect(document.activeElement?.id).toBe("memory-add-fact");
  });

  it("Memory: a search that matches nothing does not offer to add", async () => {
    getMemory.mockResolvedValue([]);
    getMemoryLayers.mockResolvedValue({ total: 0, clean: 0, tainted: 0, layers: [] });
    wrap(<Memory />);
    await screen.findByRole("button", { name: en["memory.empty.action"] });
    const user = userEvent.setup();
    await user.type(screen.getAllByPlaceholderText(en["common.search"])[0], "zzz{enter}");
    expect(await screen.findByText(en["memory.emptySearch"])).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: en["memory.empty.action"] })).toBeNull();
  });

  it("Runs: starts a task in Code", async () => {
    getRuns.mockResolvedValue([]);
    getPausedRuns.mockResolvedValue([]);
    wrap(<Runs />);
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: en["runs.empty.action"] }));
    expect(window.location.hash).toBe("#/code");
  });

  it("Decisions: opens Security", async () => {
    getDecisions.mockResolvedValue({ review_at: 0.5, allow_below: 0.3, specs: [], groups: [], recent: [], alerts: [] });
    wrap(<Decisions />);
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: en["decisions.empty.action"] }));
    expect(window.location.hash).toBe("#/settings?tab=security");
  });

  it("Skills: an empty learned list offers the library", async () => {
    const onDiscover = vi.fn();
    const skills = { isSuccess: true, isError: false, data: { stats: [], cards_read: true } } as never;
    wrap(<MineHeader skills={skills} learnedEmpty onReview={vi.fn()} onDiscover={onDiscover} />);
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: en["skills.empty.action"] }));
    expect(onDiscover).toHaveBeenCalledOnce();
  });
});

describe("when a saved setting applies", () => {
  it("says it applies now, in its own words", () => {
    wrap(<AppliesNote when="now" />);
    expect(screen.getByText(en["settings.applies.now"])).toBeInTheDocument();
  });

  it("says nothing for an unverified setting or one it does not know", () => {
    const { container } = wrap(
      <>
        <AppliesNote when="unverified" />
        <AppliesNote when="something_new" />
        <AppliesNote />
      </>,
    );
    expect(container.textContent).toBe("");
  });

  it("a Row that names its variable reads the moment from the server's table", () => {
    wrap(
      <AppliesContext.Provider value={{ CHIMERA_X: "next_launch", CHIMERA_Y: "now" }}>
        <Row label="X" env="CHIMERA_X"><span /></Row>
        <Row label="Y" env="CHIMERA_Y"><span /></Row>
        <Row label="Z" env="CHIMERA_Z" applies="commands_now"><span /></Row>
      </AppliesContext.Provider>,
    );
    expect(screen.getByText(en["settings.applies.nextLaunch"])).toBeInTheDocument();
    expect(screen.getByText(en["settings.applies.now"])).toBeInTheDocument();
    expect(screen.getByText(en["settings.applies.commandsNow"])).toBeInTheDocument();
  });
});
