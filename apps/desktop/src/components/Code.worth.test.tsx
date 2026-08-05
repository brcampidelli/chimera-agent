import { screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Code } from "@/components/Code";
import {
  getFsTree,
  getGitStatus,
  getPostureFacts,
  getRoleModels,
  getRuns,
  getWorth,
} from "@/lib/api";
import {
  emptyTree,
  gitStatus,
  postureFacts,
  roleModels,
  worthReport,
} from "@/test/code-api-mock";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", async () => (await import("@/test/code-api-mock")).makeCodeApiMock());

describe("Code — was it worth it?", () => {
  beforeEach(() => {
    vi.mocked(getFsTree).mockResolvedValue(emptyTree());
    vi.mocked(getGitStatus).mockResolvedValue(gitStatus());
    vi.mocked(getRuns).mockResolvedValue([]);
    vi.mocked(getPostureFacts).mockResolvedValue(postureFacts());
    vi.mocked(getRoleModels).mockResolvedValue(roleModels());
    vi.mocked(getWorth).mockResolvedValue(worthReport([]));
  });

  it("says there is nothing to show yet rather than showing an empty table", async () => {
    expect(vi.mocked(getWorth)).toBeDefined();
    renderWithProviders(<Code />);
    expect(await screen.findByText(/No finished runs yet/)).toBeInTheDocument();
  });

  it("reports the real cost when every run in the group was priced", async () => {
    vi.mocked(getWorth).mockResolvedValue(
      worthReport([{ profile: "balanced", runs: 12, passed: 9, usd_total: 1.2345, usd_known_runs: 12 }]),
    );
    renderWithProviders(<Code />);
    expect(await screen.findByText("$1.2345")).toBeInTheDocument();
  });

  it("refuses to invent a cost when part of the group was unpriced", async () => {
    // Rendering a partial sum — or nothing — would make the configuration that used a free or
    // unpriced tier look like the cheap one, which is the exact conclusion this panel exists to
    // let someone reach honestly.
    vi.mocked(getWorth).mockResolvedValue(
      worthReport([{ profile: "economy", runs: 12, usd_total: null, usd_known_runs: 5 }]),
    );
    renderWithProviders(<Code />);

    expect(await screen.findByText("5/12 priced")).toBeInTheDocument();
    expect(screen.queryByText(/^\$0/)).not.toBeInTheDocument();
  });

  it("shows a hollow pass beside the pass count, never folded into it", async () => {
    // A "success" that changed no file is the empty-patch failure this project measured and fixed.
    // Adding it to `passed` would let a configuration look good at exactly what it is bad at.
    vi.mocked(getWorth).mockResolvedValue(
      worthReport([{ profile: "max", runs: 12, passed: 8, unproductive: 3 }]),
    );
    renderWithProviders(<Code />);

    expect(await screen.findByText("8")).toBeInTheDocument();
    expect(screen.getByText("(−3)")).toBeInTheDocument();
  });

  it("warns while every group is still too small to read", async () => {
    vi.mocked(getWorth).mockResolvedValue(worthReport([{ runs: 3, passed: 3 }]));
    renderWithProviders(<Code />);
    expect(await screen.findByText(/read these as anecdotes, not as a result/)).toBeInTheDocument();
  });

  it("keeps saying these are not an experiment even once the data is thick", async () => {
    // The caveat is about the DESIGN of the data — observational, unrandomised — so it does not
    // stop being true when more rows arrive.
    vi.mocked(getWorth).mockResolvedValue(worthReport([{ runs: 500, passed: 480 }]));
    renderWithProviders(<Code />);

    expect(await screen.findByText(/They are a record, not an experiment/)).toBeInTheDocument();
    expect(screen.queryByText(/read these as anecdotes/)).not.toBeInTheDocument();
  });

  it("labels runs that named no profile as their own group", async () => {
    vi.mocked(getWorth).mockResolvedValue(worthReport([{ profile: null, runs: 40 }]));
    renderWithProviders(<Code />);
    expect(await screen.findByText("none")).toBeInTheDocument();
  });

  it("renders the groups in the order the server sent them", async () => {
    // The server sorts by NAME, never by outcome — re-sorting here by pass rate would turn a record
    // into a ranking these observational groups cannot support.
    vi.mocked(getWorth).mockResolvedValue(
      worthReport([
        { profile: "balanced", runs: 10, passed: 1 },
        { profile: "economy", runs: 10, passed: 9 },
      ]),
    );
    renderWithProviders(<Code />);

    const rows = (await screen.findAllByRole("row")).slice(1); // drop the header
    expect(rows[0]).toHaveTextContent("balanced");
    expect(rows[1]).toHaveTextContent("economy");
  });
});
