import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { SessionSidebar } from "@/components/code/SessionSidebar";
import { getCodeSessionRaw, listCodeSessions } from "@/lib/api";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", () => ({
  deleteCodeSession: vi.fn(),
  forkCodeSession: vi.fn(),
  getCodeSessionRaw: vi.fn(),
  listCodeSessions: vi.fn(),
  renameCodeProject: vi.fn(),
}));

/**
 * The session JSON viewer had two states for three outcomes.
 *
 * It read `raw.data ? text : loading`, so a request that FAILED rendered the loading label — for
 * ever, with no spinner and no way to tell. And 404 here is ordinary rather than exotic: another
 * window deleted the session, or it aged past the transcript cap.
 *
 * Found by using the app: deleting a session and then opening its JSON from a list that had not
 * refreshed yet. The dialog said "Carregando…" and stayed there, which reads as a hung app.
 */

const SESSIONS = [
  { id: "s1", title: "Responda apenas: 2+2", workspace: "C:\\p", turns: 1, updated_at: 2 },
];

describe("the session JSON dialog", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(listCodeSessions).mockResolvedValue(
      SESSIONS as Awaited<ReturnType<typeof listCodeSessions>>,
    );
  });

  async function abrir() {
    const user = userEvent.setup();
    renderWithProviders(<SessionSidebar
        workspace="C:\p"
        activeSession={null}
        onResume={vi.fn()}
        onNew={vi.fn()}
        onProject={vi.fn()}
      />);
    const botao = await screen.findByRole("button", { name: /Mostrar o JSON|Show the JSON/i });
    await user.click(botao);
    return user;
  }

  it("says the session could not be read instead of loading for ever", async () => {
    vi.mocked(getCodeSessionRaw).mockRejectedValue(new Error("404 Not Found"));

    await abrir();

    await waitFor(() =>
      expect(screen.getByText(/may have been deleted|pode ter sido apagada/i)).toBeInTheDocument(),
    );
    expect(screen.queryByText(/^Loading…$|^Carregando…$/)).toBeNull();
  });

  it("still shows the JSON when it loads", async () => {
    // The control. Reporting failure unconditionally would pass the test above and break the
    // feature — this dialog exists to show the text, not to apologise.
    vi.mocked(getCodeSessionRaw).mockResolvedValue({
      text: '{"session_id":"s1"}',
      bytes: 19,
    } as Awaited<ReturnType<typeof getCodeSessionRaw>>);

    await abrir();

    await waitFor(() => expect(screen.getByText(/"session_id"/)).toBeInTheDocument());
    expect(screen.queryByText(/may have been deleted|pode ter sido apagada/i)).toBeNull();
  });
});
