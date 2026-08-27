import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { Cron, whenOf } from "@/components/Cron";
import { getCron, getCronResults } from "@/lib/api";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", () => ({
  getCronSilence: vi.fn(),
  getCron: vi.fn(),
  getCronResults: vi.fn(),
  createCron: vi.fn(),
  enableCron: vi.fn(),
  disableCron: vi.fn(),
  deleteCron: vi.fn(),
}));

function job(over: Record<string, unknown> = {}) {
  return {
    id: "j1",
    name: "resumo do site",
    trigger: "cron",
    schedule: "0 7 * * *",
    action: "liste os arquivos",
    enabled: true,
    next_run: null,
    last_run: null,
    last_status: "ok",
    last_error: null,
    consecutive_failures: 0,
    created_by: "human",
    workspace: null,
    deliver_to: null,
    ...over,
  };
}

function result(over: Record<string, unknown> = {}) {
  return {
    at: 1_787_000_000,
    job_id: "j1",
    name: "resumo do site",
    action: "liste os arquivos",
    answer: "Existem 3 arquivos: dados.json, index.html e sobre.html.",
    delivered: null,
    delivery_detail: "",
    ...over,
  };
}

/**
 * What the schedule answered.
 *
 * Every dispatch has appended a line to `cron_results.jsonl` since the daemon existed, and the only
 * code that touched that file was the code that wrote it. So the screen that creates a schedule
 * promised to "save each result" and offered no way to read one — a job could run every night for a
 * month, answer well every time, and its owner would never see a word of it.
 */
describe("a schedule shows what it answered", () => {
  beforeEach(() => {
    vi.mocked(getCron).mockReset().mockResolvedValue([job()] as never);
    vi.mocked(getCronResults).mockReset().mockResolvedValue([result()] as never);
  });

  it("shows the latest answer, folded", async () => {
    renderWithProviders(<Cron />);

    const resumo = await screen.findByText(/answered/i);
    await userEvent.click(resumo);

    expect(screen.getByText(/Existem 3 arquivos/)).toBeTruthy();
  });

  it("says nothing about answers for a schedule that has not run", async () => {
    // The control: an empty disclosure on every new schedule would read as "it ran and said
    // nothing", which is a different and worse claim than "it has not run".
    vi.mocked(getCronResults).mockResolvedValue([] as never);
    renderWithProviders(<Cron />);

    await screen.findByText("resumo do site");
    expect(screen.queryByText(/answered/i)).toBeNull();
  });

  it("marks a delivery that failed, and stays quiet about one nobody asked for", async () => {
    // Two different facts. Saying "not delivered" about a job that never named a webhook tells
    // somebody their webhook is broken when they never set one.
    vi.mocked(getCronResults).mockResolvedValue([
      result({ delivered: false, delivery_detail: "HTTP 401" }),
    ] as never);
    renderWithProviders(<Cron />);

    expect(await screen.findByText(/delivery failed/i)).toBeTruthy();
  });

  it("stays quiet when no delivery was asked for", async () => {
    vi.mocked(getCronResults).mockResolvedValue([result({ delivered: null })] as never);
    renderWithProviders(<Cron />);

    await screen.findByText(/answered/i);
    expect(screen.queryByText(/delivery failed/i)).toBeNull();
  });

  it("pairs each answer with its own schedule", async () => {
    // Two jobs, one answer. Rendering the newest answer on every row would attribute one job's
    // output to another — which on a screen about unattended work is a lie nobody is present to
    // catch.
    vi.mocked(getCron).mockResolvedValue([job(), job({ id: "j2", name: "outro" })] as never);
    vi.mocked(getCronResults).mockResolvedValue([result({ job_id: "j2", answer: "só do j2" })] as never);
    renderWithProviders(<Cron />);

    await screen.findByText("outro");
    // Exactly one disclosure, on the job that actually produced it.
    expect(screen.getAllByText(/answered/i)).toHaveLength(1);
  });
});

describe("whenOf", () => {
  it("renders in the reader's own time zone", () => {
    // The point of the scheduler fix is that a job set for 7am fires at 7am where the person is.
    // Printing UTC on this screen would undo that.
    const texto = whenOf(1_787_000_000);
    expect(texto).toBeTruthy();
    expect(texto).toBe(
      new Date(1_787_000_000 * 1000).toLocaleString(undefined, {
        day: "2-digit",
        month: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
      }),
    );
  });

  it("says nothing rather than 1970 for a missing timestamp", () => {
    expect(whenOf(0)).toBe("");
  });
});

describe("which of a schedule's answers is shown", () => {
  beforeEach(() => {
    vi.mocked(getCron).mockReset().mockResolvedValue([job()] as never);
  });

  it("is the newest, not the oldest", async () => {
    // The list arrives newest-first, and keeping the FIRST per job is what makes the row show the
    // latest. Without that guard the last one wins — the oldest answer in the window — and the
    // screen would confidently report last week's output as what the job just did.
    //
    // Found by sabotage: removing the guard left all seven other tests passing, because none of
    // them had two answers for one job.
    vi.mocked(getCronResults).mockResolvedValue([
      result({ at: 1_787_000_100, answer: "RECENTE" }),
      result({ at: 1_787_000_000, answer: "ANTIGO" }),
    ] as never);
    renderWithProviders(<Cron />);

    await userEvent.click(await screen.findByText(/answered/i));

    expect(screen.getByText("RECENTE")).toBeTruthy();
    expect(screen.queryByText("ANTIGO")).toBeNull();
  });
});
