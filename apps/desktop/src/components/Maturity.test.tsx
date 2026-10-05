import { screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Maturity } from "@/components/Maturity";
import { getBenchmarks, getMaturity } from "@/lib/api";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", () => ({
  getMaturity: vi.fn(),
  getBenchmarks: vi.fn(),
}));

const mockGetMaturity = vi.mocked(getMaturity);
const mockGetBenchmarks = vi.mocked(getBenchmarks);

const surface = (name: string, proven: number, total: number, level: string) => ({
  name,
  proven,
  total,
  ratio: proven / total,
  level,
  missing: proven === total ? [] : ["x.missing"],
});

describe("Maturity — the band says what was counted", () => {
  beforeEach(() => {
    mockGetBenchmarks.mockResolvedValue({ available: false } as never);
  });

  it("names a full surface by its test files, never as a release grade", async () => {
    // The scorecard globs test-file NAMES. It used to call a full surface "GA", a claim about the
    // product that the glob cannot carry; the screen showed it for every surface.
    mockGetMaturity.mockResolvedValue({
      available: true,
      source: "live",
      proven: 6,
      total: 8,
      ratio: 0.75,
      level: "partial",
      surfaces: [surface("fusion", 5, 5, "present"), surface("memory", 1, 3, "sparse")],
      weakest: { name: "memory", ratio: 1 / 3 },
      generated_for: null,
    } as never);

    renderWithProviders(<Maturity />);

    expect(await screen.findByText("Test files present")).toBeInTheDocument();
    expect(screen.getByText("Test files partly present")).toBeInTheDocument();
    expect(screen.getByText("Test files mostly missing")).toBeInTheDocument();
    expect(screen.queryByText("GA")).not.toBeInTheDocument();
  });

  it("labels the count and the band as test files, not as proof or a maturity level", async () => {
    // The badge stopped saying "GA", but the tile beside it still read "Coverage-IDs proven" under
    // a "Level" field on a screen titled "Maturity": the same file-name glob, graded as proof.
    mockGetMaturity.mockResolvedValue({
      available: true,
      source: "live",
      proven: 6,
      total: 8,
      ratio: 0.75,
      level: "partial",
      surfaces: [surface("fusion", 5, 5, "present")],
      weakest: { name: "fusion", ratio: 1 },
      generated_for: null,
    } as never);

    renderWithProviders(<Maturity />);

    expect(await screen.findByText("Coverage-IDs with a test file")).toBeInTheDocument();
    expect(screen.getByText("Test-file band")).toBeInTheDocument();
    expect(screen.queryByText("Coverage-IDs proven")).not.toBeInTheDocument();
    expect(screen.queryByText("Maturity")).not.toBeInTheDocument();
  });
});
