import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { Conversation } from "@/components/code/Conversation";
import {
  getCodeSession,
  listShares,
  listWorks,
  streamCodeTurn,
  streamSessionLive,
  type SessionLiveFrame,
} from "@/lib/api";
import type { CodeChartFrame } from "@/lib/chart/spec";
import { scriptTurn } from "@/test/code-api-mock";
import { renderWithProviders } from "@/test/utils";

// Vega is a dynamic import, and the first one of a run can take seconds under a loaded suite.
// Loaded once up front, so each test's wait measures the drawing, not the download.
beforeAll(async () => {
  await import("@/lib/chart/render");
}, 60000);

vi.mock("@/lib/api", async () => (await import("@/test/code-api-mock")).makeCodeApiMock());
// The check off the app's thread runs in a Worker, which jsdom does not have (its protocol is tested
// in preflight.test.ts); here it lets every chart through, so these tests see the drawing.
vi.mock("@/lib/chart/preflight", () => ({ preflight: async () => ({ verdict: "draw" }) }));

/**
 * A chart the agent drew, drawn where it was asked for (study 29, P6.1).
 *
 * `render_chart` wrote a file and the conversation showed a tool row naming it; seeing the chart
 * meant finding the file. The turn now carries the spec, and the conversation gives each chart a
 * card of its own — on the screen that sent the turn and on one that follows it live.
 */
const SPEC = {
  data: { values: [{ a: "A", b: 5 }, { a: "B", b: 8 }] },
  mark: "bar",
  encoding: { x: { field: "a", type: "nominal" }, y: { field: "b", type: "quantitative" } },
};

function chart(over: Partial<CodeChartFrame> = {}): CodeChartFrame {
  return { path: "sales.html", format: "html", title: "Sales", spec: SPEC, withheld: null, bytes: 150, ...over };
}

function mount(onOpenFile = vi.fn(), resumeSession?: string) {
  return renderWithProviders(
    <Conversation
      workspace="/proj"
      openFile={null}
      resumeSession={resumeSession}
      posture={{ reach: "workspace" as never, approval: "ask" as never }}
      profile={"balanced" as never}
      onHandOff={() => {}}
      onBatch={() => {}}
      onEdited={() => {}}
      busyElsewhere={false}
      controls={null}
      onOpenFile={onOpenFile}
    />,
  );
}

describe("a chart in the conversation", () => {
  beforeEach(() => {
    vi.mocked(streamCodeTurn).mockReset();
    vi.mocked(streamSessionLive).mockReset().mockResolvedValue(null);
    vi.mocked(listShares).mockReset().mockResolvedValue({ shares: [] });
    vi.mocked(listWorks).mockReset().mockResolvedValue({ works: [] });
    localStorage.clear();
  });

  it("gets a card of its own under the turn that drew it, one per chart", async () => {
    vi.mocked(streamCodeTurn).mockImplementation(
      scriptTurn({ charts: [chart(), chart({ path: "costs.html", title: "Costs" })] }),
    );
    const open = vi.fn();
    mount(open);

    await userEvent.type(await screen.findByRole("textbox"), "chart the sales{Enter}");

    const cards = await screen.findAllByRole("region", { name: "Chart" });
    expect(cards).toHaveLength(2);
    expect(within(cards[0]).getByText("Sales")).toBeInTheDocument();
    expect(within(cards[1]).getByText("Costs")).toBeInTheDocument();
    await waitFor(() => expect(cards[0].querySelector("svg")).not.toBeNull(), { timeout: 10000 });

    await userEvent.click(within(cards[1]).getByRole("button", { name: "Open costs.html" }));
    expect(open).toHaveBeenCalledWith("costs.html");
  });

  it("minimises to one line like every other card", async () => {
    vi.mocked(streamCodeTurn).mockImplementation(scriptTurn({ charts: [chart()] }));
    mount();
    await userEvent.type(await screen.findByRole("textbox"), "chart the sales{Enter}");
    await screen.findByRole("region", { name: "Chart" });

    await userEvent.click(screen.getByRole("button", { name: /^Minimise Chart$|^Minimize Chart$/ }));

    expect(screen.queryByRole("region", { name: "Chart" })).toBeNull();
    expect(document.querySelector('[data-card="chart"]')?.textContent).toContain("Sales");
  });

  it("reaches a screen that follows the turn live", async () => {
    vi.mocked(getCodeSession).mockResolvedValue({
      id: "s1",
      workspace: "/proj",
      exchanges: [],
      running_turn: {
        turn_id: "t1", session_id: "s1", workspace: "/proj", message: "chart it",
        started_at: 1, live_since: 3, transcript_saved: false,
      },
    });
    let onFrame: ((f: SessionLiveFrame) => void) | null = null;
    vi.mocked(streamSessionLive).mockImplementation((_sid, _since, handler) => {
      onFrame = handler;
      return new Promise<string | null>(() => {});
    });
    mount(vi.fn(), "s1");

    await waitFor(() => expect(onFrame).not.toBeNull());
    const send = (seq: number, event: string, payload: Record<string, unknown>) =>
      act(() => onFrame?.({ session_seq: seq, event, turn_id: "t1", author: "", payload }));
    await send(4, "turn_started", { message: "chart it" });
    await send(5, "chart", chart() as unknown as Record<string, unknown>);

    const card = await screen.findByRole("region", { name: "Chart" });
    expect(within(card).getByText("Sales")).toBeInTheDocument();
  });
});
