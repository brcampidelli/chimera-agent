import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { SearchPanel } from "@/components/editor/SearchPanel";
import { MachinePanel } from "@/components/MachinePanel";
import { getResources, searchFiles } from "@/lib/api";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", () => ({ searchFiles: vi.fn(), getResources: vi.fn() }));

/**
 * The two things P3 puts on screen, and the one property they share: a gap is shown as a gap.
 *
 * A search that stopped early must not look complete, and a reading nobody could take must not
 * render as zero. Both failures are silent and both get believed.
 */

function result(over: Partial<Awaited<ReturnType<typeof searchFiles>>> = {}) {
  return {
    hits: [
      { path: "src/app.py", line: 4, text: "def connect():", start: 4, end: 11 },
      { path: "src/app.py", line: 9, text: "    connect()", start: 4, end: 11 },
      { path: "notes.md", line: 1, text: "connect early", start: 0, end: 7 },
    ],
    engine: "ripgrep",
    capped: false,
    timed_out: false,
    elapsed_ms: 12,
    error: "",
    ...over,
  };
}

async function searchFor(text: string) {
  const user = userEvent.setup();
  await user.type(screen.getByPlaceholderText(/Find in project/i), `${text}{Enter}`);
}

beforeEach(() => {
  vi.mocked(searchFiles).mockResolvedValue(result() as never);
  vi.mocked(getResources).mockResolvedValue({
    cpu_percent: 12.5,
    cpu_count: 24,
    memory: { total_mb: 32189, used_mb: 24267, percent: 75.4 },
    process_mb: 38,
    gpus: [{ name: "RTX 5070", vram_total_mb: 8151, vram_used_mb: 2366, utilisation: 78 }],
    notes: [],
  } as never);
});

describe("SearchPanel", () => {
  it("does not search until you ask", async () => {
    // A search per keystroke is a subprocess per keystroke on a repository that may take a second
    // to walk — and the results flicker between prefixes of what you meant.
    const user = userEvent.setup();
    renderWithProviders(<SearchPanel workspace="/repo" activePath={null} onOpen={() => {}} />);

    await user.type(screen.getByPlaceholderText(/Find in project/i), "conn");

    expect(searchFiles).not.toHaveBeenCalled();
  });

  it("groups the hits by file", async () => {
    // "Which files is this in" first, "which lines" second. A flat list of two hundred answers the
    // second question at the cost of the first.
    renderWithProviders(<SearchPanel workspace="/repo" activePath={null} onOpen={() => {}} />);
    await searchFor("connect");

    expect(await screen.findByText(/src\/app\.py/)).toBeInTheDocument();
    expect(screen.getByText(/notes\.md/)).toBeInTheDocument();
    // The count is per file, so the two hits in app.py read as two.
    expect(screen.getByText("(2)")).toBeInTheDocument();
  });

  it("opens the file a hit points at", async () => {
    const onOpen = vi.fn();
    const user = userEvent.setup();
    renderWithProviders(<SearchPanel workspace="/repo" activePath={null} onOpen={onOpen} />);
    await searchFor("connect");

    // Found by the button's whole text, not by a string: the highlight splits the line into three
    // nodes, so `getByText("connect early")` matches nothing — which is a fact about the rendering
    // this test exists to exercise rather than a reason to stop highlighting.
    await screen.findByText(/notes\.md/);
    const row = screen
      .getAllByRole("button")
      .find((button) => button.textContent?.includes("connect early"));
    await user.click(row as HTMLElement);

    expect(onOpen).toHaveBeenCalledWith("notes.md", 1);
  });

  it("highlights the span the server measured", async () => {
    // Never by re-searching the line here: a case-insensitive or regex query re-searched in the
    // browser highlights the wrong span, and reads as a rendering bug rather than a wrong answer.
    renderWithProviders(<SearchPanel workspace="/repo" activePath={null} onOpen={() => {}} />);
    await searchFor("connect");

    const marks = await screen.findAllByText("connect");
    expect(marks.some((el) => el.tagName === "MARK")).toBe(true);
  });

  it("says when it stopped early rather than looking complete", async () => {
    vi.mocked(searchFiles).mockResolvedValue(result({ capped: true }) as never);
    renderWithProviders(<SearchPanel workspace="/repo" activePath={null} onOpen={() => {}} />);
    await searchFor("connect");

    expect(await screen.findByText(/Too many matches/i)).toBeInTheDocument();
  });

  it("distinguishes running out of time from having too many answers", async () => {
    vi.mocked(searchFiles).mockResolvedValue(result({ timed_out: true, hits: [] }) as never);
    renderWithProviders(<SearchPanel workspace="/repo" activePath={null} onOpen={() => {}} />);
    await searchFor("connect");

    expect(await screen.findByText(/ran out of time/i)).toBeInTheDocument();
  });

  it("names the fallback engine", async () => {
    // A silent swap to a slower engine that ignores .gitignore returns different results for the
    // same query on a different machine, and nobody would know which one they got.
    vi.mocked(searchFiles).mockResolvedValue(result({ engine: "python" }) as never);
    renderWithProviders(<SearchPanel workspace="/repo" activePath={null} onOpen={() => {}} />);
    await searchFor("connect");

    expect(await screen.findByText(/without ripgrep/i)).toBeInTheDocument();
  });

  it("keeps quiet about the engine when it is the real one", async () => {
    renderWithProviders(<SearchPanel workspace="/repo" activePath={null} onOpen={() => {}} />);
    await searchFor("connect");

    await screen.findByText(/notes\.md/);
    expect(screen.queryByText(/without ripgrep/i)).toBeNull();
  });

  it("shows a bad pattern as a message, not as an empty result", async () => {
    vi.mocked(searchFiles).mockResolvedValue(
      result({ hits: [], error: "invalid pattern: missing )" }) as never,
    );
    renderWithProviders(<SearchPanel workspace="/repo" activePath={null} onOpen={() => {}} />);
    await searchFor("(unclosed");

    expect(await screen.findByText(/invalid pattern/i)).toBeInTheDocument();
  });

  it("passes the toggles to the server rather than filtering here", async () => {
    const user = userEvent.setup();
    renderWithProviders(<SearchPanel workspace="/repo" activePath={null} onOpen={() => {}} />);

    await user.click(screen.getByRole("button", { name: /Regular expression/i }));
    await searchFor("def .+");

    await waitFor(() =>
      expect(searchFiles).toHaveBeenCalledWith("/repo", "def .+", {
        regex: true,
        caseSensitive: false,
      }),
    );
  });
});

describe("MachinePanel", () => {
  it("shows what it measured", async () => {
    renderWithProviders(<MachinePanel />);

    expect(await screen.findByText("13%")).toBeInTheDocument(); // CPU, rounded
    expect(screen.getByText(/24 cores/)).toBeInTheDocument();
    expect(screen.getByText("RTX 5070")).toBeInTheDocument();
  });

  it("renders an absent reading as unavailable, never as zero", async () => {
    // The whole reason this panel exists in this shape. 0% VRAM reads as "the GPU is idle", and on
    // an AMD or Apple machine that is a claim about hardware we cannot see — one that gets believed.
    vi.mocked(getResources).mockResolvedValue({
      cpu_percent: null,
      cpu_count: 8,
      memory: { total_mb: null, used_mb: null, percent: null },
      process_mb: null,
      gpus: [],
      notes: ["install the 'desktop' extra for CPU and memory (psutil)"],
    } as never);

    renderWithProviders(<MachinePanel />);

    expect(await screen.findAllByText("unavailable")).toHaveLength(2); // CPU and memory
    expect(screen.queryByText("0%")).toBeNull();
  });

  it("shows the server's note about what it could not read", async () => {
    // What turns a gap from a mystery into an instruction.
    vi.mocked(getResources).mockResolvedValue({
      cpu_percent: 5,
      cpu_count: 8,
      memory: { total_mb: 100, used_mb: 50, percent: 50 },
      process_mb: 10,
      gpus: [],
      notes: ["GPU memory is read through nvidia-smi; on Darwin without it, it is unavailable rather than zero"],
    } as never);

    renderWithProviders(<MachinePanel />);

    expect(await screen.findByText(/unavailable rather than zero/i)).toBeInTheDocument();
  });

  it("reports a GPU whose driver would not say as unavailable", async () => {
    vi.mocked(getResources).mockResolvedValue({
      cpu_percent: 5,
      cpu_count: 8,
      memory: { total_mb: 100, used_mb: 50, percent: 50 },
      process_mb: 10,
      gpus: [{ name: "Quadro P1000", vram_total_mb: 4096, vram_used_mb: null, utilisation: null }],
      notes: [],
    } as never);

    renderWithProviders(<MachinePanel />);

    expect(await screen.findByText("Quadro P1000")).toBeInTheDocument();
    expect(screen.getAllByText("unavailable").length).toBeGreaterThan(0);
  });
});
