import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { GuestError, getGuestSession, sendGuestTurn, streamGuestLive, type GuestLiveFrame } from "@/guest/api";
import { GuestPage } from "@/guest/GuestPage";
import { I18nProvider } from "@/lib/i18n";

vi.mock("@/guest/api", async () => {
  const real = await vi.importActual<typeof import("@/guest/api")>("@/guest/api");
  return {
    ...real,
    tokenFromLocation: vi.fn(() => "share-token"),
    getGuestSession: vi.fn(),
    sendGuestTurn: vi.fn(async () => undefined),
    streamGuestLive: vi.fn(async () => null),
  };
});

/**
 * The page a guest opens from a share link: one conversation, who is here, a box to speak into
 * it — and the honest states around it: the link no longer opens anything, the owner is being
 * asked something only the owner can answer, the connection dropped and is coming back.
 */

function mount() {
  return render(
    <I18nProvider>
      <GuestPage />
    </I18nProvider>,
  );
}

function live(seq: number, event: string, turnId: string, author: string, payload: Record<string, unknown> = {}): GuestLiveFrame {
  return { session_seq: seq, event, turn_id: turnId, author, payload };
}

function openStream() {
  let onFrame: ((f: GuestLiveFrame) => void) | null = null;
  vi.mocked(streamGuestLive).mockImplementation((_t, _since, _name, handler) => {
    onFrame = handler;
    return new Promise<string | null>(() => {});
  });
  return () => onFrame;
}

const SESSION = {
  session_id: "s1",
  workspace_name: "shop",
  exchanges: [
    { you: "fix the login", answer: "Done.", tools: [{ name: "read_file" }], edits: [], done: { answer: "Done." } },
    { you: "and the logout", answer: "Also done.", tools: [], edits: [], done: { answer: "Also done.", author: "Ana" } },
  ],
  presence: ["owner"],
  seq: 9,
};

async function join(name = "Bia") {
  const user = userEvent.setup();
  mount();
  await user.type(screen.getByRole("textbox", { name: /your name/i }), name);
  await user.click(screen.getByRole("button", { name: /join/i }));
  return user;
}

describe("the guest page", () => {
  beforeEach(() => {
    vi.mocked(getGuestSession).mockReset().mockResolvedValue(SESSION);
    vi.mocked(sendGuestTurn).mockReset().mockResolvedValue(undefined);
    vi.mocked(streamGuestLive).mockReset().mockResolvedValue(null);
    localStorage.clear();
  });

  it("asks for a name, then shows the conversation with who asked what, and follows from where it was", async () => {
    const handler = openStream();
    await join();
    await waitFor(() => expect(screen.getByText("and the logout")).toBeInTheDocument());
    const authors = screen.getAllByTestId("guest-author").map((el) => el.textContent);
    expect(authors).toEqual(["owner", "Ana"]);
    expect(screen.getByText(/shop/)).toBeInTheDocument();
    expect(screen.getByText("read_file")).toBeInTheDocument();
    await waitFor(() => expect(streamGuestLive).toHaveBeenCalledWith("share-token", 9, "Bia", expect.any(Function), expect.anything()));
    expect(localStorage.getItem("chimera.guest.name")).toBe("Bia");
    expect(handler()).not.toBeNull();
  });

  it("draws a turn as it happens, names the others here, and says when the owner is being asked", async () => {
    const handler = openStream();
    await join();
    await waitFor(() => expect(handler()).not.toBeNull());
    act(() => {
      handler()!(live(10, "presence", "", "", { names: ["owner", "Bia", "Ana"] }));
      handler()!(live(11, "turn_started", "t3", "", { message: "delete the cache" }));
      handler()!(live(12, "approval", "t3", "", { id: "q1", action: "rm -rf .cache" }));
    });
    expect(screen.getByTestId("guest-presence")).toHaveTextContent("owner, Ana");
    expect(screen.getByTestId("guest-presence")).not.toHaveTextContent("Bia");
    expect(screen.getByText("delete the cache")).toBeInTheDocument();
    expect(screen.getByTestId("guest-waiting")).toBeInTheDocument();
    act(() => {
      handler()!(live(13, "token", "t3", "", { text: "Cache " }));
      handler()!(live(14, "token", "t3", "", { text: "cleared." }));
      handler()!(live(15, "done", "t3", "", { answer: "Cache cleared." }));
    });
    expect(screen.queryByTestId("guest-waiting")).not.toBeInTheDocument();
    expect(screen.getByText("Cache cleared.")).toBeInTheDocument();
  });

  it("sends what was typed, as the guest, and clears the box", async () => {
    openStream();
    const user = await join("Bia");
    await waitFor(() => expect(streamGuestLive).toHaveBeenCalled());
    const box = screen.getByRole("textbox", { name: /ask the agent/i });
    await user.type(box, "add a logout button");
    await user.click(screen.getByRole("button", { name: /send/i }));
    await waitFor(() => expect(sendGuestTurn).toHaveBeenCalledWith("share-token", "add a logout button", "Bia"));
    expect(box).toHaveValue("");
  });

  it("says when the link no longer opens a conversation", async () => {
    vi.mocked(getGuestSession).mockRejectedValue(new GuestError("this link no longer opens a conversation", 401));
    await join();
    await waitFor(() => expect(screen.getByTestId("guest-gone")).toBeInTheDocument());
    expect(screen.queryByRole("textbox", { name: /ask the agent/i })).not.toBeInTheDocument();
  });

  it("names the owner in the guest's language, not by the name the owner's window subscribes under", async () => {
    localStorage.setItem("chimera.lang", "pt");
    const handler = openStream();
    const user = userEvent.setup();
    mount();
    await user.type(screen.getByRole("textbox"), "Bia");
    await user.click(screen.getByRole("button"));
    await waitFor(() => expect(handler()).not.toBeNull());
    act(() => {
      handler()!(live(10, "presence", "", "", { names: ["owner", "Bia", "Ana"] }));
    });
    expect(screen.getByTestId("guest-presence")).toHaveTextContent("dono, Ana");
    expect(screen.getByTestId("guest-presence")).not.toHaveTextContent("owner");
  });
});
