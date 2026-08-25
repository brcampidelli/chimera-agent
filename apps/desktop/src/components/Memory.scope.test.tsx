import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { Memory } from "@/components/Memory";
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
        <Memory />
      </QueryClientProvider>
    </I18nProvider>,
  );
}

afterEach(() => {
  getMemory.mockReset();
  getMemoryLayers.mockReset();
});

/**
 * Which facts belong everywhere.
 *
 * What the agent learns is now saved into the folder it was learned in — that is the default, and
 * the reason for it was measured: a note about one project was arriving as context on ordinary
 * requests in another. The consequence for this screen is that "everywhere" became the exception,
 * and an exception nobody can see is a fact that quietly stops arriving with nothing saying why.
 *
 * Only the exception is labelled. A badge on every row would state the ordinary case out loud and
 * bury the one that differs.
 */
describe("Memory — the facts that belong everywhere", () => {
  function stub(facts: unknown[]) {
    getMemory.mockResolvedValue(facts);
    getMemoryLayers.mockResolvedValue({ layers: [], total: 0, by_source: [] });
  }

  const global = {
    id: "1",
    content: "prefiro respostas curtas",
    kind: "persona",
    provenance: "clean",
    source: "chimera",
    project: null,
  };
  const doProjeto = {
    id: "2",
    content: "este projeto usa pytest",
    kind: "semantic",
    provenance: "clean",
    source: "chimera",
    project: "/home/x/chimera",
  };

  it("labels the one that applies everywhere", async () => {
    stub([global, doProjeto]);
    renderMemory();

    const linha = (await screen.findByText(global.content)).parentElement!;
    expect(within(linha).getByText(/everywhere/i)).toBeInTheDocument();
  });

  it("leaves a project's own fact unlabelled", async () => {
    stub([global, doProjeto]);
    renderMemory();

    const linha = (await screen.findByText(doProjeto.content)).parentElement!;
    expect(within(linha).queryByText(/everywhere/i)).toBeNull();
  });

  it("treats a fact from before the field existed as belonging everywhere", async () => {
    // An older store has no `project` key at all. Absent must read the same as null here, or the
    // screen tells a different story about the same fact depending on when it was written.
    const antigo = { ...global, id: "3", content: "fato antigo sem escopo" };
    delete (antigo as { project?: unknown }).project;
    stub([antigo]);
    renderMemory();

    const linha = (await screen.findByText(antigo.content)).parentElement!;
    expect(within(linha).getByText(/everywhere/i)).toBeInTheDocument();
  });
});
