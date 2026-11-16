import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Work } from "@/components/Work";
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

/** Render Work and open the tab this panel now lives on.
 *
 * The assertions below are unchanged from when these ran against the Code screen — only the host
 * moved. Keeping them verbatim is the point: if relocating a panel had changed what it DOES, these
 * would say so, and a rewritten test could not. */
async function openTab(name: RegExp) {
  renderWithProviders(<Work />);
  await userEvent.click(await screen.findByRole("tab", { name }));
}

describe("Work — was it worth it?", () => {
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
    await openTab(/Valeu a pena|Was it worth/);
    expect(await screen.findByText(/No finished runs yet/)).toBeInTheDocument();
  });

  it("reports the real cost when every run in the group was priced", async () => {
    vi.mocked(getWorth).mockResolvedValue(
      worthReport([{ profile: "balanced", runs: 12, passed: 9, passed_by_verifier: 3, usd_total: 1.2345, usd_known_runs: 12 }]),
    );
    await openTab(/Valeu a pena|Was it worth/);
    expect(await screen.findByText("$1.2345")).toBeInTheDocument();
  });

  it("shows the cost per resolved task with its count and price coverage", async () => {
    vi.mocked(getWorth).mockResolvedValue(
      worthReport([
        {
          runs: 4,
          passed: 2,
          resolved_n: 2,
          passed_by_verifier: 1,
          usd_total: 0.4,
          usd_known_runs: 4,
          price_coverage: 1,
          usd_per_resolved: 0.2,
          usd_per_verified: 0.4,
        },
      ]),
    );
    await openTab(/Valeu a pena|Was it worth/);
    expect(await screen.findByTestId("usd-per-resolved")).toHaveTextContent("$0.2000");
    expect(screen.getByTestId("usd-per-verified")).toHaveTextContent("$0.4000");
    expect(screen.getByText(/2 resolved.*100% of runs priced/)).toBeInTheDocument();
  });

  it("leaves cost per resolved blank, with the coverage, when a run was unpriced", async () => {
    vi.mocked(getWorth).mockResolvedValue(
      worthReport([
        {
          runs: 3,
          passed: 3,
          resolved_n: 3,
          usd_total: null,
          usd_known_runs: 2,
          price_coverage: 0.6667,
          usd_per_resolved: null,
          usd_per_verified: null,
        },
      ]),
    );
    await openTab(/Valeu a pena|Was it worth/);
    expect(await screen.findByTestId("usd-per-resolved")).toHaveTextContent("—");
    expect(screen.getByTestId("usd-per-verified")).toHaveTextContent("—");
    expect(screen.getByText(/3 resolved.*67% of runs priced/)).toBeInTheDocument();
  });

  it("leaves cost per resolved blank when nothing passed, and says it is not a ranking", async () => {
    vi.mocked(getWorth).mockResolvedValue(
      worthReport([
        { runs: 2, passed: 0, resolved_n: 0, passed_by_verifier: 0, usd_total: 0.02, usd_per_resolved: null, usd_per_verified: null },
      ]),
    );
    await openTab(/Valeu a pena|Was it worth/);
    expect(await screen.findByTestId("usd-per-resolved")).toHaveTextContent("—");
    expect(screen.getByText(/Not an experiment: it is not ranked/)).toBeInTheDocument();
  });

  it("refuses to invent a cost when part of the group was unpriced", async () => {
    // Rendering a partial sum — or nothing — would make the configuration that used a free or
    // unpriced tier look like the cheap one, which is the exact conclusion this panel exists to
    // let someone reach honestly.
    vi.mocked(getWorth).mockResolvedValue(
      worthReport([{ profile: "economy", runs: 12, usd_total: null, usd_known_runs: 5 }]),
    );
    await openTab(/Valeu a pena|Was it worth/);

    expect(await screen.findByText("5/12 priced")).toBeInTheDocument();
    expect(screen.queryByText(/^\$0/)).not.toBeInTheDocument();
  });

  it("shows a hollow pass beside the pass count, never folded into it", async () => {
    // A "success" that changed no file is the empty-patch failure this project measured and fixed.
    // Adding it to `passed` would let a configuration look good at exactly what it is bad at.
    vi.mocked(getWorth).mockResolvedValue(
      worthReport([{ profile: "max", runs: 12, passed: 8, unproductive: 3 }]),
    );
    await openTab(/Valeu a pena|Was it worth/);

    expect(await screen.findByText("8")).toBeInTheDocument();
    expect(screen.getByText("(−3)")).toBeInTheDocument();
  });

  it("warns while every group is still too small to read", async () => {
    vi.mocked(getWorth).mockResolvedValue(worthReport([{ runs: 3, passed: 3 }]));
    await openTab(/Valeu a pena|Was it worth/);
    expect(await screen.findByText(/read these as anecdotes, not as a result/)).toBeInTheDocument();
  });

  it("keeps saying these are not an experiment even once the data is thick", async () => {
    // The caveat is about the DESIGN of the data — observational, unrandomised — so it does not
    // stop being true when more rows arrive.
    vi.mocked(getWorth).mockResolvedValue(worthReport([{ runs: 500, passed: 480 }]));
    await openTab(/Valeu a pena|Was it worth/);

    expect(await screen.findByText(/They are a record, not an experiment/)).toBeInTheDocument();
    expect(screen.queryByText(/read these as anecdotes/)).not.toBeInTheDocument();
  });

  it("labels runs that named no profile as their own group", async () => {
    vi.mocked(getWorth).mockResolvedValue(worthReport([{ profile: null, runs: 40 }]));
    await openTab(/Valeu a pena|Was it worth/);
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
    await openTab(/Valeu a pena|Was it worth/);

    const rows = (await screen.findAllByRole("row")).slice(1); // drop the header
    expect(rows[0]).toHaveTextContent("balanced");
    expect(rows[1]).toHaveTextContent("economy");
  });
});

describe("Work — which kind of pass", () => {
  beforeEach(() => {
    vi.mocked(getFsTree).mockResolvedValue(emptyTree());
    vi.mocked(getGitStatus).mockResolvedValue(gitStatus());
    vi.mocked(getRuns).mockResolvedValue([]);
  });

  it("says how many passes a command judged, not just how many passed", async () => {
    // Without this the number merged two claims: a run approved by an exit code, and one approved
    // by a model reading the answer text. Both passed; only one was verified.
    vi.mocked(getWorth).mockResolvedValue(
      worthReport([{ profile: "balanced", runs: 4, passed: 3, passed_by_verifier: 1 }]),
    );
    await openTab(/Valeu a pena|Was it worth/);

    const row = (await screen.findAllByRole("row"))[1];
    expect(row).toHaveTextContent("3");
    expect(row).toHaveTextContent(/1.*with tests|1.*com testes/);
  });
});

describe("Work - was it worth it? against a server older than the per-resolved fields", () => {
  it("shows a dash, not an error, when the response lacks the new fields", async () => {
    vi.mocked(getFsTree).mockResolvedValue(emptyTree());
    vi.mocked(getGitStatus).mockResolvedValue(gitStatus());
    vi.mocked(getRuns).mockResolvedValue([]);
    vi.mocked(getPostureFacts).mockResolvedValue(postureFacts());
    vi.mocked(getRoleModels).mockResolvedValue(roleModels());
    // The shape an older server sends: no resolved_n, price_coverage, usd_per_resolved or usd_per_verified.
    const old = worthReport([{ runs: 4, passed: 2, usd_total: 0.4, usd_known_runs: 4 }]);
    old.profiles = old.profiles.map((g) => {
      const { resolved_n: _a, price_coverage: _b, usd_per_resolved: _c, usd_per_verified: _d, ...rest } = g;
      return rest;
    });
    vi.mocked(getWorth).mockResolvedValue(old);
    await openTab(/Valeu a pena|Was it worth/);
    expect(await screen.findByTestId("usd-per-resolved")).toHaveTextContent("—");
    expect(screen.getByTestId("usd-per-verified")).toHaveTextContent("—");
    expect(screen.getByText(/2 resolved.*100% of runs priced/)).toBeInTheDocument();
  });
});
