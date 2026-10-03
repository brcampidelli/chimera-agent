import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { MessagingCard } from "@/components/Settings";
import { getMessaging, startMessaging, stopMessaging } from "@/lib/api";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", () => ({
  // Settings shows the inline-suggestion acceptance rate now.
  getCompletionStats: vi.fn(async () => ({ accepted: 0, dismissed: 0, rate: null, mean_ms: null })),
  getMessaging: vi.fn(),
  startMessaging: vi.fn(),
  stopMessaging: vi.fn(),
}));

type D = { configured: boolean; running: boolean; error: string | null };

function setup(discord: D) {
  vi.mocked(getMessaging).mockResolvedValue({ discord } as never);
  vi.mocked(startMessaging).mockResolvedValue({ discord: { ...discord, running: true } } as never);
  vi.mocked(stopMessaging).mockResolvedValue({ discord: { ...discord, running: false } } as never);
}

describe("MessagingCard", () => {
  beforeEach(() => vi.clearAllMocks());

  it("saves the Discord token from the UI (no terminal)", async () => {
    const user = userEvent.setup();
    const save = vi.fn();
    setup({ configured: false, running: false, error: null });
    renderWithProviders(<MessagingCard save={save} />);

    await user.click(await screen.findByRole("button", { name: /^Set$/i }));
    const tokenField = screen.getByPlaceholderText(/paste/i);
    await user.type(tokenField, "discord-token-123");
    // Scoped to the token's own row: the card has a second Save now, for who may talk to the bot.
    await user.click(
      within(tokenField.parentElement as HTMLElement).getByRole("button", { name: /^Save$/i }),
    );

    expect(save).toHaveBeenCalledWith({ CHIMERA_DISCORD_BOT_TOKEN: "discord-token-123" });
  });

  it("turning the toggle on starts the bot and persists auto-start", async () => {
    const user = userEvent.setup();
    const save = vi.fn();
    setup({ configured: true, running: false, error: null });
    renderWithProviders(<MessagingCard save={save} />);

    await user.click(await screen.findByRole("switch"));

    await waitFor(() => expect(startMessaging).toHaveBeenCalledWith("discord"));
    expect(save).toHaveBeenCalledWith({ CHIMERA_APP_MESSAGING: "true" });
  });

  it("turning it off stops the bot", async () => {
    const user = userEvent.setup();
    const save = vi.fn();
    setup({ configured: true, running: true, error: null });
    renderWithProviders(<MessagingCard save={save} />);

    await user.click(await screen.findByRole("switch"));

    await waitFor(() => expect(stopMessaging).toHaveBeenCalledWith("discord"));
    expect(save).toHaveBeenCalledWith({ CHIMERA_APP_MESSAGING: "false" });
  });

  it("shows the adapter error when the bot died (e.g. a bad token)", async () => {
    setup({ configured: true, running: false, error: "RuntimeError: bad token" });
    renderWithProviders(<MessagingCard save={vi.fn()} />);

    expect(await screen.findByText(/bad token/i)).toBeInTheDocument();
  });

  it("asks for a Telegram token on the Telegram card, not a Discord one", async () => {
    // The card has taken a `platform` since Telegram was added and used it for the title — while
    // the two rows inside kept the Discord strings. So the Telegram card asked for a "Discord bot
    // token", and someone reading that field has every reason to paste the wrong one into it.
    vi.mocked(getMessaging).mockResolvedValue({
      telegram: { configured: false, running: false, error: null },
    } as never);
    renderWithProviders(
      <MessagingCard
        save={vi.fn()}
        platform="telegram"
        tokenEnv="CHIMERA_TELEGRAM_BOT_TOKEN"
      />,
    );

    expect(await screen.findByText("Telegram bot token")).toBeInTheDocument();
    expect(screen.getByRole("switch", { name: "Run the Telegram bot" })).toBeInTheDocument();
    expect(screen.queryByText(/Discord/)).not.toBeInTheDocument();
  });

  it("warns that a configured bot with no allowlist answers anyone", async () => {
    // The adapters always took an allowlist and nothing filled it, so the bot answered whoever
    // reached it. Empty still means anyone — the owner's decision — but the card has to say so.
    setup({ configured: true, running: true, error: null });
    renderWithProviders(<MessagingCard save={vi.fn()} allowed={[]} />);

    expect(
      await screen.findByText(/Anyone who can message the Discord bot gets a turn/),
    ).toBeInTheDocument();
  });

  it("does not warn once the owner listed who may talk to it", async () => {
    setup({ configured: true, running: true, error: null });
    renderWithProviders(<MessagingCard save={vi.fn()} allowed={["42"]} />);

    expect(await screen.findByRole("switch")).toBeInTheDocument();
    expect(screen.queryByText(/Anyone who can message/)).not.toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "Who can talk to the bot" })).toHaveValue("42");
  });

  it("saves the list to the platform's own variable and says it applies at the next launch", async () => {
    const user = userEvent.setup();
    const save = vi.fn();
    vi.mocked(getMessaging).mockResolvedValue({
      telegram: { configured: true, running: false, error: null },
    } as never);
    renderWithProviders(
      <MessagingCard
        save={save}
        platform="telegram"
        tokenEnv="CHIMERA_TELEGRAM_BOT_TOKEN"
        allowedApplies="next_launch"
      />,
    );

    const field = await screen.findByRole("textbox", { name: "Who can talk to the bot" });
    await user.type(field, "111, 222");
    await user.click(
      within(field.parentElement as HTMLElement).getByRole("button", { name: /^Save$/i }),
    );

    expect(save).toHaveBeenCalledWith({ CHIMERA_TELEGRAM_ALLOWED_USERS: "111, 222" });
    expect(screen.getByText(/next time you start the app/i)).toBeInTheDocument();
  });
});
