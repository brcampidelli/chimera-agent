import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { MessagingCard } from "@/components/Settings";
import { getMessaging, startMessaging, stopMessaging } from "@/lib/api";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", () => ({
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
    await user.type(screen.getByPlaceholderText(/paste/i), "discord-token-123");
    await user.click(screen.getByRole("button", { name: /^Save$/i }));

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
});
