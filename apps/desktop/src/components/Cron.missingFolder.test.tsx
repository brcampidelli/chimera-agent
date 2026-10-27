import { screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { Cron } from "@/components/Cron";
import { getCron, getCronResults, getCronSilence } from "@/lib/api";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", () => ({
  getCronSilence: vi.fn(),
  getCronResults: vi.fn(),
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
    workspace_missing: false,
    ...over,
  };
}

/**
 * A schedule whose folder is gone still reads "Active" — the engine does not decide whether the folder
 * comes back. Measured on a real install: "resumo do site" pointed at a folder that did not exist and
 * showed no sign of it. The row now names the problem beside the job's name.
 */
describe("a schedule whose folder is gone", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(getCronSilence).mockResolvedValue({ overdue: [] } as never);
    vi.mocked(getCronResults).mockResolvedValue([] as never);
  });

  it("says the folder is not on this computer, beside the name", async () => {
    vi.mocked(getCron).mockResolvedValue([
      job({ workspace: "C:\\Users\\brcam\\Desktop\\teste-chimera\\site-institucional", workspace_missing: true }),
    ] as never);
    renderWithProviders(<Cron />);

    const name = await screen.findByText("resumo do site");
    const row = name.closest("div")!.parentElement!;
    expect(within(row).getByText("Folder not found on this computer")).toBeInTheDocument();
  });

  it("still shows it as active, because the schedule itself is not switched off", async () => {
    vi.mocked(getCron).mockResolvedValue([
      job({ workspace: "C:\\gone", workspace_missing: true }),
    ] as never);
    renderWithProviders(<Cron />);

    await screen.findByText("resumo do site");
    expect(screen.getByText("Active")).toBeInTheDocument();
  });

  it("does not warn about a job that works in an existing folder", async () => {
    vi.mocked(getCron).mockResolvedValue([job({ workspace: "C:\\here", workspace_missing: false })] as never);
    renderWithProviders(<Cron />);

    await screen.findByText("resumo do site");
    expect(screen.queryByText("Folder not found on this computer")).not.toBeInTheDocument();
  });

  it("does not warn about a job with no folder, which runs at the process root by design", async () => {
    vi.mocked(getCron).mockResolvedValue([job({ workspace: null, workspace_missing: false })] as never);
    renderWithProviders(<Cron />);

    await screen.findByText("resumo do site");
    expect(screen.queryByText("Folder not found on this computer")).not.toBeInTheDocument();
  });
});
