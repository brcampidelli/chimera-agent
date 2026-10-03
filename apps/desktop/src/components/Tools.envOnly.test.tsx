import { screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Tools } from "@/components/Tools";
import { getConfig, getTools } from "@/lib/api";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", () => ({
  getTools: vi.fn(),
  getConfig: vi.fn(),
  patchConfig: vi.fn(),
}));

const DATA = {
  count: 0,
  tools: [],
  unavailable: [
    {
      name: "web_search", description: "Search the web.", kind: "key",
      variables: ["TAVILY_API_KEY"], requires: "", switchable: false, default_on: false,
      in_settings: true,
    },
    {
      name: "send_email", description: "Send an email.", kind: "key",
      variables: ["CHIMERA_SMTP_HOST", "CHIMERA_SMTP_USER", "CHIMERA_SMTP_PASSWORD"], requires: "",
      switchable: false, default_on: false, in_settings: false,
    },
  ],
};

/**
 * Every `key` row used to say "add it in Settings". For the Tavily key that is true. For SMTP, IMAP
 * and the ICS URL it is not: those variables are outside the config allowlist, so Settings has no
 * field for them and a save would be refused. The server now says which rows Settings can serve
 * (`in_settings`), and the others name the file they are really set in.
 */
describe("Tools — a key row says where the key is really set", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(getTools).mockResolvedValue(DATA as never);
    vi.mocked(getConfig).mockResolvedValue({ autonomy: { denied_tools: [] } } as never);
  });

  it("sends a key Settings can save to Settings", async () => {
    renderWithProviders(<Tools />);

    expect(await screen.findByText("Needs TAVILY_API_KEY — add it in Settings.")).toBeInTheDocument();
  });

  it("names the .env for a variable Settings has no field for, and never says Settings has one", async () => {
    renderWithProviders(<Tools />);

    const line = await screen.findByText(/CHIMERA_SMTP_HOST/);
    expect(line).toHaveTextContent(/\.env/);
    expect(line).not.toHaveTextContent(/add it in Settings/);
  });
});
