import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { Cron } from "@/components/Cron";
import { createCron, getCron } from "@/lib/api";
import { WORKSPACE_KEY } from "@/lib/workspace";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", () => ({
  getCronSilence: vi.fn(),
  getCron: vi.fn(),
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
    action: "liste os arquivos do projeto",
    enabled: true,
    next_run: null,
    last_run: null,
    last_status: null,
    last_error: null,
    consecutive_failures: 0,
    created_by: "human",
    workspace: null,
    ...over,
  };
}

/**
 * A schedule has to know which folder it works in.
 *
 * The screen sent nothing, so every job ran at whatever root the process was started with — the
 * install directory on a packaged build. Measured on a real install: a 07:00 job asking for "the
 * project's files" walked 4757 files of the app's own installation and was abandoned at 1800s,
 * five nights running.
 *
 * The choice is fixed when the job is written, not read when it fires: a schedule outlives the
 * project the user happens to have open on any given morning.
 */
describe("a schedule knows which project it works in", () => {
  beforeEach(() => {
    localStorage.clear();
    vi.mocked(getCron).mockReset().mockResolvedValue([]);
    vi.mocked(createCron).mockReset().mockResolvedValue({} as never);
  });

  async function preencherEEnviar() {
    const user = userEvent.setup();
    await user.type(screen.getByPlaceholderText(/name/i), "resumo");
    await user.type(screen.getByPlaceholderText(/what should Chimera do/i), "liste os arquivos");
    await user.click(screen.getByRole("button", { name: /schedule/i }));
  }

  it("sends the chosen project when the job is created", async () => {
    localStorage.setItem(WORKSPACE_KEY, "/projects/cafe-aurora");
    renderWithProviders(<Cron />);

    await preencherEEnviar();

    expect(vi.mocked(createCron).mock.calls[0]?.[0]).toMatchObject({
      workspace: "/projects/cafe-aurora",
    });
  });

  it("sends null when no project has been chosen", async () => {
    // The control. `""` would be stored as a chosen root of "" on the job, which then reads as a
    // decision rather than as its absence.
    renderWithProviders(<Cron />);

    await preencherEEnviar();

    expect(vi.mocked(createCron).mock.calls[0]?.[0]).toMatchObject({ workspace: null });
  });

  it("shows the folder on a job that has one", async () => {
    vi.mocked(getCron).mockResolvedValue([job({ workspace: "/projects/cafe-aurora" })] as never);
    renderWithProviders(<Cron />);

    expect(await screen.findByText("/projects/cafe-aurora")).toBeTruthy();
  });

  it("shows nothing rather than a guess on a job that has none", async () => {
    // Every job written before this field existed. Printing the app's own root there would state
    // a choice nobody made — the exact claim this row exists to stop making.
    vi.mocked(getCron).mockResolvedValue([job()] as never);
    renderWithProviders(<Cron />);

    await screen.findByText("resumo do site");
    expect(screen.queryByText(/Chimera[\\/]/)).toBeNull();
  });
});
