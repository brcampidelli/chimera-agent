import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { Memory } from "@/components/Memory";
import { TooltipProvider } from "@/components/ui/tooltip";
import { I18nProvider } from "@/lib/i18n";

const getMemory = vi.fn();
const getMemoryLayers = vi.fn();

vi.mock("@/lib/api", () => ({
  getMemory: (...a: unknown[]) => getMemory(...a),
  getMemoryLayers: (...a: unknown[]) => getMemoryLayers(...a),
  addMemory: vi.fn(),
  deleteMemory: vi.fn(),
}));

function renderMemory() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <I18nProvider>
      <QueryClientProvider client={qc}>
        <TooltipProvider>
          <Memory />
        </TooltipProvider>
      </QueryClientProvider>
    </I18nProvider>,
  );
}

afterEach(() => {
  getMemory.mockReset();
  getMemoryLayers.mockReset();
});

const clean = {
  id: "c1",
  content: "prefers short answers",
  kind: "persona",
  provenance: "clean",
  source: "chat",
  project: null,
};
const tainted = {
  id: "t1",
  content: "the build uses poetry",
  kind: "semantic",
  provenance: "tainted",
  source: "chimera",
  project: null,
};

/** What `/api/memory/layers` really sends for these two facts: all four kinds, two of them at 0. */
const layers = {
  total: 2,
  clean: 1,
  tainted: 1,
  layers: [
    { kind: "working", count: 0, clean: 0, tainted: 0 },
    { kind: "episodic", count: 0, clean: 0, tainted: 0 },
    { kind: "semantic", count: 1, clean: 0, tainted: 1 },
    { kind: "persona", count: 1, clean: 1, tainted: 0 },
  ],
  by_source: [
    { source: "chat", count: 1 },
    { source: "chimera", count: 1 },
  ],
  semantic_embeddings_enabled: false,
};

/**
 * The Memory screen as the owner reads it.
 *
 * The capture showed three big counters and two "By kind" rows of zeros above the fold, and the facts
 * — the only part a person edits — below it. "Clean / Unverified" were never explained, "2
 * unverified" could be read but not acted on, and the footer spoke in the vocabulary of the code
 * ("embeddings (opt-in)", "By source: chimera").
 */
describe("Memory — facts first, plain words", () => {
  it("puts the facts above the statistics", async () => {
    getMemory.mockResolvedValue([clean, tainted]);
    getMemoryLayers.mockResolvedValue(layers);
    renderMemory();

    const fact = await screen.findByText("prefers short answers");
    const stats = await screen.findByText("Total facts");
    // DOCUMENT_POSITION_FOLLOWING: the stats tile comes after the fact in reading order.
    expect(fact.compareDocumentPosition(stats) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(screen.getByText("Facts in memory: 2")).toBeInTheDocument();
  });

  it("does not spend a row on a kind that holds nothing", async () => {
    getMemory.mockResolvedValue([clean, tainted]);
    getMemoryLayers.mockResolvedValue(layers);
    renderMemory();

    await screen.findByText("Total facts");
    // The fact rows carry their own kind badge ("Semantic", "Persona"), so the bar rows are the only
    // place "Working" or "Episodic" could appear — and both are empty.
    expect(screen.queryByText("Working")).not.toBeInTheDocument();
    expect(screen.queryByText("Episodic")).not.toBeInTheDocument();
    expect(screen.getAllByText("Semantic").length).toBeGreaterThan(0);
  });

  it("filters the list to the unverified facts from the count", async () => {
    getMemory.mockResolvedValue([clean, tainted]);
    getMemoryLayers.mockResolvedValue(layers);
    const user = userEvent.setup();
    renderMemory();

    await screen.findByText("prefers short answers");
    await user.click(await screen.findByRole("button", { name: "Unverified: 1 — show them" }));

    expect(screen.getByText("the build uses poetry")).toBeInTheDocument();
    expect(screen.queryByText("prefers short answers")).not.toBeInTheDocument();
    expect(screen.getByText("Showing only unverified facts.")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Show all" }));
    expect(screen.getByText("prefers short answers")).toBeInTheDocument();
  });

  it("says what unverified means, and where a fact came from, in words", async () => {
    getMemory.mockResolvedValue([clean, tainted]);
    getMemoryLayers.mockResolvedValue(layers);
    renderMemory();

    await screen.findByText("Total facts");
    expect(screen.getByRole("button", { name: "What does unverified mean?" })).toBeInTheDocument();
    expect(screen.getByText(/Unverified ones were picked up from outside content/)).toBeInTheDocument();
    // "chimera" was printed raw, in monospace; the source now reads as a sentence.
    expect(screen.getByText("Saved by Chimera")).toBeInTheDocument();
    expect(screen.queryByText("chimera")).not.toBeInTheDocument();
    // The embeddings note leads with what it means for the person searching; the engineering
    // wording is behind the detail button, not on the screen.
    expect(screen.getByText(/matches the words you type, not their meaning/)).toBeInTheDocument();
    expect(screen.queryByText(/opt-in/)).not.toBeInTheDocument();
  });

  it("offers no unverified filter when every fact is clean", async () => {
    getMemory.mockResolvedValue([clean]);
    getMemoryLayers.mockResolvedValue({
      ...layers,
      total: 1,
      clean: 1,
      tainted: 0,
      by_source: [{ source: "chat", count: 1 }],
    });
    renderMemory();

    expect(await screen.findByText("Facts in memory: 1")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /show them/ })).not.toBeInTheDocument();
  });
});
