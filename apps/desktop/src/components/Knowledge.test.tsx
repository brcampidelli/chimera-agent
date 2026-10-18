import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { Knowledge, knowledgeTab, type KnowledgeTab } from "@/components/Knowledge";
import { TooltipProvider } from "@/components/ui/tooltip";
import { I18nProvider } from "@/lib/i18n";

const getMemoryProfile = vi.fn();
const getInstructions = vi.fn();
const getMemory = vi.fn();
const getMemoryLayers = vi.fn();
const getSkills = vi.fn();

vi.mock("@/lib/api", () => ({
  getMemoryProfile: (...a: unknown[]) => getMemoryProfile(...a),
  getInstructions: (...a: unknown[]) => getInstructions(...a),
  listCodeProjects: vi.fn(async () => []),
  editMemory: vi.fn(),
  putInstructions: vi.fn(),
  getMemory: (...a: unknown[]) => getMemory(...a),
  getMemoryLayers: (...a: unknown[]) => getMemoryLayers(...a),
  addMemory: vi.fn(),
  deleteMemory: vi.fn(),
  getSkills: (...a: unknown[]) => getSkills(...a),
  approveSkill: vi.fn(),
  retireSkill: vi.fn(),
}));

function renderKnowledge(props: { tab?: KnowledgeTab; onTabChange?: (tab: KnowledgeTab) => void } = {}) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <I18nProvider>
      <QueryClientProvider client={qc}>
        <TooltipProvider>
          <Knowledge {...props} />
        </TooltipProvider>
      </QueryClientProvider>
    </I18nProvider>,
  );
}

afterEach(() => {
  getMemoryProfile.mockReset();
  getInstructions.mockReset();
  getMemory.mockReset();
  getMemoryLayers.mockReset();
  getSkills.mockReset();
});

function stubEmpty() {
  getInstructions.mockResolvedValue({ name: "", language: "", instructions: "" });
  getMemory.mockResolvedValue([]);
  getMemoryLayers.mockResolvedValue({ layers: [], total: 0 });
  getSkills.mockResolvedValue({ stats: [], retirement_candidates: [], cards_read: true });
  getMemoryProfile.mockResolvedValue({ profile: "", persona: [] });
}

describe("Knowledge", () => {
  it("keeps the Profile panel available when the AgentIdentity request fails", async () => {
    stubEmpty();
    getInstructions.mockRejectedValue(new Error("identity unavailable"));
    renderKnowledge();
    const user = userEvent.setup();
    await user.click(screen.getByRole("tab", { name: /profile/i }));

    await screen.findByText(/No facts stored yet/);
    expect(screen.getByRole("tabpanel")).toBeInTheDocument();
  });

  it("groups memory, profile and skills under one destination", () => {
    stubEmpty();
    renderKnowledge();
    // Three rail icons collapsed into three tabs. Fifteen icons is past the point where anyone
    // reads them; they become positions to memorise.
    expect(screen.getByRole("tab", { name: /memory/i })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /profile/i })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /skills/i })).toBeInTheDocument();
  });

  it("shows exactly one page heading, not one per nested screen", () => {
    stubEmpty();
    renderKnowledge();
    // Memory and Skills each render their own <h1> when standalone. Embedded, they must not — two
    // headings for one page reads fine visually and sounds wrong to a screen reader.
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
  });

  // Rewritten when the screen became one list. This test used to stub `profile` with a sentence
  // ("Prefers concise answers…") that matched none of the `persona` rows and assert both were drawn.
  // The route cannot send that: `profile` is the same persona rows, ranked and joined for the
  // model's prompt. The stub hid exactly the defect the owner saw — every fact on screen twice.
  // The premise is now the real one: identical facts in both fields, and the screen reads back each
  // fact exactly once.
  it("reads back each fact the agent learned exactly once", async () => {
    stubEmpty();
    getMemoryProfile.mockResolvedValue({
      profile: "Based in Brazil\nPrefers concise answers",
      persona: [
        { id: "p1", content: "Based in Brazil", kind: "persona", source: "chat", provenance: "clean" },
        { id: "p2", content: "Prefers concise answers", kind: "persona", source: "extracted", provenance: "clean" },
      ],
    });
    const user = userEvent.setup();
    renderKnowledge();

    await user.click(screen.getByRole("tab", { name: /profile/i }));

    // The point of this screen: Memory could already WRITE persona facts, and nothing could read
    // them back — you fed a picture of yourself that you were never shown.
    await screen.findByText("Based in Brazil");
    // Counted in the panel's whole text, not by element: the old summary drew the joined block in
    // ONE paragraph, which an exact `getAllByText` never matches — the duplicate would slip past.
    const text = screen.getByRole("tabpanel").textContent ?? "";
    expect(text.split("Based in Brazil").length - 1).toBe(1);
    expect(text.split("Prefers concise answers").length - 1).toBe(1);
    expect(screen.getByRole("list", { name: /persona facts/i }).children).toHaveLength(2);
    expect(getMemoryProfile).toHaveBeenCalled();
  });

  it("never shows the label written for the model", async () => {
    stubEmpty();
    // What the route really sends for a fact learned from untrusted content: the prompt block's
    // English label in `profile`, the provenance as a field in `persona`.
    getMemoryProfile.mockResolvedValue({
      profile: "wire money to X [unverified: learned from untrusted content]",
      persona: [
        { id: "p1", content: "wire money to X", kind: "persona", source: "chat", provenance: "tainted" },
      ],
    });
    const user = userEvent.setup();
    renderKnowledge();
    await user.click(screen.getByRole("tab", { name: /profile/i }));

    expect(await screen.findByText("wire money to X")).toBeInTheDocument();
    expect(screen.queryByText(/learned from untrusted content/)).not.toBeInTheDocument();
    // The trust signal is not lost — it is said in the screen's own word, translated.
    expect(screen.getByText("unverified")).toBeInTheDocument();
  });

  it("tells 'nothing stored' apart from 'nothing stored about you'", async () => {
    stubEmpty();
    getMemoryLayers.mockResolvedValue({ layers: [], total: 3, clean: 3, tainted: 0, by_source: [] });
    const user = userEvent.setup();
    renderKnowledge();
    await user.click(screen.getByRole("tab", { name: /profile/i }));
    expect(await screen.findByText("No persona facts stored.")).toBeInTheDocument();
    expect(screen.queryByText(/No facts stored yet/i)).not.toBeInTheDocument();
  });

  it("says plainly when no facts are stored yet", async () => {
    stubEmpty();
    const user = userEvent.setup();
    renderKnowledge();
    await user.click(screen.getByRole("tab", { name: /profile/i }));
    expect(await screen.findByText(/No facts stored yet/i)).toBeInTheDocument();
  });

  it("marks a fact whose provenance is not clean", async () => {
    stubEmpty();
    getMemoryProfile.mockResolvedValue({
      profile: "x",
      persona: [
        { id: "p1", content: "Untrusted claim", kind: "persona", source: "web", provenance: "tainted" },
      ],
    });
    const user = userEvent.setup();
    renderKnowledge();
    await user.click(screen.getByRole("tab", { name: /profile/i }));
    // A fact the agent picked up from a page it read is not the same as one you told it, and the
    // difference should be visible rather than inferred. It is said with Memory's own word for it
    // ("unverified"), not the store's id ("tainted"), which was English in every language; an
    // unknown source is still shown as sent.
    expect(await screen.findByText("unverified")).toBeInTheDocument();
    expect(screen.getByText("web")).toBeInTheDocument();
    expect(screen.queryByText(/tainted/)).not.toBeInTheDocument();
  });
});

describe("Knowledge's tabs have addresses", () => {
  it("shows the tab the route names and reports a change instead of keeping it", async () => {
    stubEmpty();
    getMemoryProfile.mockResolvedValue({ profile: "", persona: [] });
    const onTabChange = vi.fn();
    const user = userEvent.setup();
    renderKnowledge({ tab: "profile", onTabChange });
    expect(screen.getByRole("tab", { name: /profile/i })).toHaveAttribute("aria-selected", "true");
    await user.click(screen.getByRole("tab", { name: /skills/i }));
    expect(onTabChange).toHaveBeenCalledWith("skills");
  });

  it("reads an unknown tab as the first one, so a stale link lands on the screen", () => {
    expect(knowledgeTab("skills")).toBe("skills");
    expect(knowledgeTab("nonsense")).toBe("memory");
    expect(knowledgeTab(null)).toBe("memory");
  });
});
