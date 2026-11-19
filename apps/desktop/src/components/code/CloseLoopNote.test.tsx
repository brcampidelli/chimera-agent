import { screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ExchangeView, type ExchangeViewProps } from "@/components/code/ExchangeView";
import type { CodeTurnDone } from "@/lib/api";
import { DICTS, type Lang, type TFunc } from "@/lib/i18n";
import { closeLoopNote } from "@/lib/closeLoopNote";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", async () => (await import("@/test/code-api-mock")).makeCodeApiMock());

const DONE: CodeTurnDone = {
  answer: "The label changed.", steps: 1, stopped_reason: "final", tool_names: [], model: "fake",
  prompt_tokens: 1, completion_tokens: 1, usd: 0, context_peak_tokens: 1, route_meta: null,
  close_loop_note: "payload",
  close_loop: { skipped_reason: "verification did not pass for this turn", skipped_code: "verification_failed" },
};
function translator(lang: Lang): TFunc {
  return (key, params) => (DICTS[lang][key] ?? DICTS.en[key] ?? key).replace(/\{(\w+)\}/g,
    (_match, name: string) => String(params?.[name] ?? `{${name}}`));
}
function props(done: CodeTurnDone): ExchangeViewProps {
  return {
    e: { you: "Update the label", answer: done.answer, tools: [], edits: [], todos: [], done },
    i: 0, isLast: true, busy: false, busyElsewhere: false, fuse: false, runLive: false,
    workspace: "/project", sessionId: "s", transcriptView: "normal", suggestions: undefined,
    hasOpenFile: false, hasOpenDesign: false, taskCardRef: { current: null },
    cards: { mode: () => "open", set: vi.fn(), setKind: vi.fn(), closedInTurn: () => 0, showTurn: vi.fn() },
    actions: {
      openFile: vi.fn(), openDesign: vi.fn(), send: () => true, undo: async () => undefined,
      handOff: vi.fn(), startTaskCard: vi.fn(), removeTaskCard: vi.fn(), pickSuggestion: vi.fn(),
    },
  };
}

describe("the payload-managed close-loop note", () => {
  beforeEach(() => localStorage.clear());
  it.each([
    ["en", "verification did not pass for this turn"],
    ["pt", "a verificação deste turno não passou"],
  ] as const)("renders the real exchange in %s, once", (lang, expected) => {
    localStorage.setItem("chimera.lang", lang);
    renderWithProviders(<ExchangeView {...props(DONE)} />);
    expect(screen.getByText("The label changed.")).toBeInTheDocument();
    expect(screen.getByTestId("close-loop-note")).toHaveTextContent(expected);
    expect(screen.getAllByTestId("close-loop-note")).toHaveLength(1);
    if (lang === "pt") expect(screen.queryByText(/verification did not pass/)).not.toBeInTheDocument();
  });
  it.each(["future_v2", "toString", undefined])("shows the raw reason for unknown or missing code %s", code => {
    const state = { skipped_code: code, skipped_reason: "future server detail" };
    expect(closeLoopNote({ ...DONE, close_loop: state }, translator("pt"))).toContain("future server detail");
  });
  it("does not infer a code by matching an old English sentence", () => {
    expect(closeLoopNote({ ...DONE, close_loop: { skipped_reason: DONE.close_loop?.skipped_reason } }, translator("pt")))
      .toContain("verification did not pass for this turn");
  });
  it("keeps a legacy answer intact without rendering another note", () => {
    const done = { ...DONE, close_loop_note: undefined, answer: "The label changed.\n\nClose loop: CI pending." };
    renderWithProviders(<ExchangeView {...props(done)} />);
    expect(screen.queryByTestId("close-loop-note")).not.toBeInTheDocument();
    expect(screen.getAllByText("Close loop: CI pending.")).toHaveLength(1);
  });
  it("preserves links, successful status and fix count on replay, then updates the watcher state once", () => {
    const initial: CodeTurnDone = { ...DONE, close_loop: {
      committed: true, commit: "abcdef12345", branch: "work", ci: "pending", fix_attempts: 1,
      pr_url: "https://example.com/org/repo/pull/1",
    } };
    // JSON round trip is the stored receipt; the same ExchangeView receives live done frames.
    const stored: CodeTurnDone = JSON.parse(JSON.stringify(initial));
    const view = renderWithProviders(<ExchangeView {...props(stored)} />);
    expect(screen.getByTestId("close-loop-note")).toHaveTextContent("CI pending");
    expect(screen.getByRole("link", { name: "pull request https://example.com/org/repo/pull/1" }))
      .toHaveAttribute("href", "https://example.com/org/repo/pull/1");
    view.rerender(<ExchangeView {...props({ ...stored, close_loop: { ...stored.close_loop, ci: "passed", merged: true } })} />);
    expect(screen.getAllByTestId("close-loop-note")).toHaveLength(1);
    expect(screen.getByTestId("close-loop-note")).toHaveTextContent("CI passed; fix attempts 1; merged");
    expect(screen.queryByText(/CI pending/)).not.toBeInTheDocument();
  });
  it.each(["merging_off", "unreadable_diff", "exceptions", "merge_refused"])("translates known merge decision %s", code => {
    const note = closeLoopNote({ ...DONE, close_loop: {
      ci: "passed", merge_blocked_code: code, merge_blocked_reason: "static English reason",
    } }, translator("pt"));
    expect(note).toContain("CI aprovado");
    expect(note).not.toContain("static English reason");
    expect(note).toContain("deixado aberto para o dono");
  });
  it.each(["exceptions", "merge_refused"])("preserves technical diagnostics as literal text for %s", code => {
    localStorage.setItem("chimera.lang", "pt");
    const detail = code === "exceptions"
      ? "touches billing (invoices.py) — left open for the owner"
      : "merge refused: branch policy: **labels** [reference](https://example.com/note)";
    renderWithProviders(<ExchangeView {...props({ ...DONE, close_loop: {
      ci: "passed", merge_blocked_code: code, merge_blocked_reason: detail,
      pr_url: "https://github.com/org/repo/pull/1",
    } })} />);
    const note = screen.getByTestId("close-loop-note");
    expect(note).toHaveTextContent("merge não realizado");
    expect(note).toHaveTextContent(detail);
    expect(note.querySelector("strong")).toBeNull();
    expect(screen.getAllByRole("link")).toHaveLength(1);
    expect(screen.getByRole("link")).toHaveAttribute("href", "https://github.com/org/repo/pull/1");
  });
  it("renders unknown raw reasons as text rather than Markdown", () => {
    const raw = "future reason: **label** [reference](https://example.com/note)";
    renderWithProviders(<ExchangeView {...props({ ...DONE, close_loop: {
      skipped_code: "future_v3", skipped_reason: raw,
    } })} />);
    expect(screen.getByTestId("close-loop-note")).toHaveTextContent(raw);
    expect(screen.getByTestId("close-loop-note").querySelector("strong")).toBeNull();
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
  });

  it("keeps a non-HTTPS PR address literal and uses the app anchor contract for HTTPS", () => {
    const raw = "http://github.com/org/repo/pull/1";
    const view = renderWithProviders(<ExchangeView {...props({ ...DONE, close_loop: { ci: "passed", merged: true, pr_url: raw } })} />);
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
    expect(screen.getByTestId("close-loop-note")).toHaveTextContent(raw);
    view.rerender(<ExchangeView {...props({ ...DONE, close_loop: {
      ci: "passed", merged: true, pr_url: "https://github.com/org/repo/pull/1",
    } })} />);
    expect(screen.getByRole("link")).toHaveAttribute("target", "_blank");
    expect(screen.getByRole("link")).toHaveAttribute("rel", "noreferrer");
  });

});
