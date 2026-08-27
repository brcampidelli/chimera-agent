import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { Cron, hostOf } from "@/components/Cron";
import { createCron, getCron } from "@/lib/api";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", () => ({
  getCronSilence: vi.fn(),
  getCron: vi.fn(),
  createCron: vi.fn(),
  enableCron: vi.fn(),
  disableCron: vi.fn(),
  deleteCron: vi.fn(),
}));

const WEBHOOK = "https://discord.com/api/webhooks/123456789/segredo-que-nao-pode-vazar";

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
    last_status: null,
    last_error: null,
    consecutive_failures: 0,
    created_by: "human",
    workspace: null,
    deliver_to: null,
    ...over,
  };
}

/**
 * Where a scheduled answer goes.
 *
 * `deliver_to` was on the model from the start and appeared in exactly two places in the codebase:
 * its own declaration, and a line copying it into the result file. Nothing read it to send
 * anything, and no screen offered to set it — so every answer a schedule ever produced went into a
 * JSONL nobody opens.
 *
 * A webhook URL rather than a bot token, because a bot needs an application, an invite and a server
 * you administer, and a webhook is a URL you copy out of a channel's settings.
 */
describe("a schedule can deliver its answer to chat", () => {
  beforeEach(() => {
    localStorage.clear();
    vi.mocked(getCron).mockReset().mockResolvedValue([]);
    vi.mocked(createCron).mockReset().mockResolvedValue({} as never);
  });

  async function preencher(webhook?: string) {
    const user = userEvent.setup();
    await user.type(screen.getByPlaceholderText(/name/i), "resumo");
    await user.type(screen.getByPlaceholderText(/what should Chimera do/i), "liste os arquivos");
    if (webhook) await user.type(screen.getByLabelText(/webhook/i), webhook);
    await user.click(screen.getByRole("button", { name: /schedule/i }));
  }

  it("sends the webhook the user pasted", async () => {
    renderWithProviders(<Cron />);
    await preencher(WEBHOOK);

    expect(vi.mocked(createCron).mock.calls[0]?.[0]).toMatchObject({ deliver_to: WEBHOOK });
  });

  it("sends null when the field is left empty", async () => {
    // The control: delivery is optional, and `""` would round-trip as "deliver to nowhere in
    // particular" rather than as "do not deliver".
    renderWithProviders(<Cron />);
    await preencher();

    expect(vi.mocked(createCron).mock.calls[0]?.[0]).toMatchObject({ deliver_to: null });
  });

  it("shows the host on a job that delivers, and never the URL", async () => {
    // A webhook URL is a credential — whoever reads it off a screen can post in that channel.
    vi.mocked(getCron).mockResolvedValue([job({ deliver_to: WEBHOOK })] as never);
    renderWithProviders(<Cron />);

    expect(await screen.findByText(/discord\.com/)).toBeTruthy();
    expect(screen.queryByText(/segredo-que-nao-pode-vazar/)).toBeNull();
    expect(screen.queryByText(/123456789/)).toBeNull();
  });

  it("says nothing about delivery on a job that has none", async () => {
    vi.mocked(getCron).mockResolvedValue([job()] as never);
    renderWithProviders(<Cron />);

    await screen.findByText("resumo do site");
    expect(screen.queryByText(/delivers to/i)).toBeNull();
  });
});

describe("hostOf", () => {
  it("keeps the host and drops the secret path", () => {
    expect(hostOf(WEBHOOK)).toBe("discord.com");
    expect(hostOf("https://hooks.slack.com/services/T/B/x")).toBe("hooks.slack.com");
  });

  it("shows a malformed value back rather than hiding it", () => {
    // Something the user typed wrong. Hiding it leaves them staring at a row that says nothing,
    // with no way to see what needs fixing.
    expect(hostOf("nao-e-uma-url")).toBe("nao-e-uma-url");
  });
});
