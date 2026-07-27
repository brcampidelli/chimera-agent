import { useState } from "react";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { AgentStatusBar } from "@/components/shell/AgentStatusBar";
import { AppShell } from "@/components/shell/AppShell";
import { AgentProvider, type AgentState } from "@/lib/agent-context";
import { I18nProvider } from "@/lib/i18n";
import type { TurnReport } from "@/lib/types";

vi.mock("@/components/VersionBadge", () => ({ VersionBadge: () => null }));

function idle(overrides: Partial<AgentState> = {}): AgentState {
  return { status: "idle", tools: [], report: null, busy: false, stop: () => {}, ...overrides };
}

function report(): TurnReport {
  return {
    session_id: "s1",
    answer: "ok",
    prompt_tokens: 1000,
    completion_tokens: 204,
    cache_read_tokens: 0,
    cache_write_tokens: 0,
    usd: 0.0031,
    tool_names: [],
    memory_facts_used: 0,
    memory_layer: null,
    steps: 1,
    stopped_reason: "done",
  };
}

function renderBar(state: AgentState, onOpenUsage?: () => void) {
  return render(
    <I18nProvider>
      <AgentProvider value={state}>
        <AgentStatusBar onOpenUsage={onOpenUsage} />
      </AgentProvider>
    </I18nProvider>,
  );
}

describe("AgentStatusBar", () => {
  it("shows what the agent is doing", () => {
    renderBar(idle({ status: "streaming" }));
    expect(screen.getByRole("status")).toHaveTextContent(/streaming/i);
  });

  it("offers Stop while busy, from anywhere", () => {
    // The regression test for this phase's whole point. Stop used to live only in the composer, so
    // navigating away mid-run left a running agent and no way to halt it.
    const stop = vi.fn();
    renderBar(idle({ busy: true, status: "streaming", stop }));
    expect(screen.getByRole("button", { name: /stop/i })).toBeInTheDocument();
  });

  it("actually stops the run", async () => {
    const stop = vi.fn();
    const user = userEvent.setup();
    renderBar(idle({ busy: true, status: "streaming", stop }));
    await user.click(screen.getByRole("button", { name: /stop/i }));
    expect(stop).toHaveBeenCalledOnce();
  });

  it("hides Stop when nothing is running", () => {
    renderBar(idle());
    expect(screen.queryByRole("button", { name: /stop/i })).not.toBeInTheDocument();
  });

  it("reports the turn's cost and links to the breakdown", async () => {
    // This link is what makes it safe to move the usage screen out of the primary nav: the number
    // stays in front of you, and the detail is one click away.
    const onOpenUsage = vi.fn();
    const user = userEvent.setup();
    renderBar(idle({ report: report() }), onOpenUsage);

    // Prompt + completion. Matched loosely on the digits: the thousands separator comes from the
    // runtime's locale data, which differs between a full-ICU Node and a small-ICU one.
    expect(screen.getByText(/1.?204 tok/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /0\.0031/ }));
    expect(onOpenUsage).toHaveBeenCalledOnce();
  });

  it("names the last tool that ran", () => {
    renderBar(idle({ tools: [{ name: "read_file", ok: true }, { name: "web_search", ok: false }] }));
    expect(screen.getByText(/web_search/)).toBeInTheDocument();
  });
});

describe("AppShell", () => {
  function Harness() {
    const [view, setView] = useState("chat");
    return (
      <I18nProvider>
        <AgentProvider value={idle({ busy: true, status: "streaming" })}>
          <AppShell
            viewKey={view}
            viewLabel={view}
            rail={<button onClick={() => setView("settings")}>Go to settings</button>}
            context={view === "chat" ? <div>Sessions</div> : undefined}
            inspector={view === "chat" ? <div>Activity</div> : undefined}
          >
            <div>Content for {view}</div>
          </AppShell>
        </AgentProvider>
      </I18nProvider>
    );
  }

  it("keeps the agent visible after navigating away from chat", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    expect(screen.getByText("Sessions")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Go to settings" }));

    // The context and inspector columns are chat's, and they go...
    expect(screen.queryByText("Sessions")).not.toBeInTheDocument();
    expect(screen.queryByText("Activity")).not.toBeInTheDocument();
    // ...but the agent does not. This is the difference between an agent app and a menu of screens.
    expect(screen.getAllByRole("status").some((n) => /streaming/i.test(n.textContent ?? ""))).toBe(
      true,
    );
    expect(screen.getByRole("button", { name: /stop/i })).toBeInTheDocument();
  });

  it("moves focus into the new screen when the view changes", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    await user.click(screen.getByRole("button", { name: "Go to settings" }));
    // Focus used to stay on the rail button, so a keyboard user re-tabbed from the top of the app
    // on every navigation.
    await waitFor(() => expect(screen.getByRole("main")).toHaveFocus());
  });

  it("does not steal focus on first paint", () => {
    render(<Harness />);
    // Grabbing focus on load would fight a screen reader's own start-of-document announcement.
    expect(screen.getByRole("main")).not.toHaveFocus();
  });

  it("puts a skip link ahead of the rail", () => {
    render(<Harness />);
    const link = screen.getByRole("link", { name: /skip to content/i });
    expect(link).toHaveAttribute("href", "#main");
    expect(screen.getByRole("main")).toHaveAttribute("id", "main");
  });
});
