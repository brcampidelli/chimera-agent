import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { Servers } from "@/components/Servers";
import { renderWithProviders as render } from "@/test/utils";

/**
 * The screen that points this app at somebody else's Chimera.
 *
 * What it must get right is mostly what it must REFUSE. A remote over plain http leaks the token on
 * every request; a remote with no token is an agent anybody who finds the address can drive. Both
 * are invisible from here — nothing on screen would look wrong — so the refusals are the feature.
 *
 * The handshake is mocked; its own behaviour is covered in `server.test.ts`. What is asserted here
 * is that each outcome reaches the user as something they can act on, which is the part a passing
 * unit test of the network layer says nothing about.
 */
vi.mock("@/lib/server", async (importOriginal) => {
  // The pure rules (which URLs are refused, how a base is normalised) are the real ones: mocking
  // them would let this file agree with itself about a policy it does not implement.
  const real = await importOriginal<typeof import("@/lib/server")>();
  return { ...real, handshake: vi.fn() };
});

const { handshake } = await import("@/lib/server");

const preencher = async (url: string, token: string, nome = "VPS") => {
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: /add a server/i }));
  await user.type(screen.getByLabelText(/^name$/i), nome);
  await user.type(screen.getByLabelText(/^address$/i), url);
  if (token) await user.type(screen.getByLabelText(/^token$/i), token);
  return user;
};

describe("choosing which Chimera the app talks to", () => {
  beforeEach(() => {
    localStorage.clear();
    vi.clearAllMocks();
  });

  it("starts on this computer, and offers no way to remove it", () => {
    render(<Servers />);
    expect(screen.getByText(/this computer/i)).toBeInTheDocument();
    expect(screen.getByText(/in use/i)).toBeInTheDocument();
    // Nothing to remove yet, and the local row must never offer it: it is not a stored server, it
    // is the app's own backend.
    expect(screen.queryByRole("button", { name: /remove/i })).not.toBeInTheDocument();
  });

  it("refuses plain http off the loopback, and says why in terms of the token", async () => {
    render(<Servers />);
    const user = await preencher("http://chimera.exemplo.com", "tk");
    await user.click(screen.getByRole("button", { name: /^test$/i }));

    expect(await screen.findByRole("status")).toHaveTextContent(/https is required/i);
    // And it never asked the network: a refusal that still sends the token defeats itself.
    expect(handshake).not.toHaveBeenCalled();
  });

  it("refuses a remote with no token", async () => {
    render(<Servers />);
    const user = await preencher("https://chimera.exemplo.com", "");
    await user.click(screen.getByRole("button", { name: /^test$/i }));

    expect(await screen.findByRole("status")).toHaveTextContent(/a token is required/i);
    expect(handshake).not.toHaveBeenCalled();
  });

  it("reports the version when the server answers", async () => {
    vi.mocked(handshake).mockResolvedValue({
      ok: true,
      version: "0.43.0",
      appVersion: "0.43.0",
      sameVersion: true,
    });
    render(<Servers />);
    const user = await preencher("https://chimera.exemplo.com", "tk");
    await user.click(screen.getByRole("button", { name: /^test$/i }));

    expect(await screen.findByRole("status")).toHaveTextContent(/Chimera 0\.43\.0/);
  });

  it("warns, and does not refuse, when the versions disagree", async () => {
    // Refusing would strand the common case — a server one release behind — and the screen that
    // fixes it is the one being refused.
    vi.mocked(handshake).mockResolvedValue({
      ok: true,
      version: "0.38.0",
      appVersion: "0.43.0",
      sameVersion: false,
    });
    render(<Servers />);
    const user = await preencher("https://chimera.exemplo.com", "tk");
    await user.click(screen.getByRole("button", { name: /^add$/i }));

    expect(await screen.findByRole("status")).toHaveTextContent(/0\.38\.0.*0\.43\.0/);
    // Warned and still added: the list has it.
    await waitFor(() => expect(screen.getByText("VPS")).toBeInTheDocument());
  });

  it("names the origin to allow when it cannot reach the server", async () => {
    // The browser refuses to say whether a cross-origin request was blocked or the host was down,
    // on purpose. Guessing one would send the user to fix the wrong thing, so the message names
    // both and hands over the exact value the operator has to set.
    vi.mocked(handshake).mockResolvedValue({ ok: false, reason: "unreachable" });
    render(<Servers />);
    const user = await preencher("https://chimera.exemplo.com", "tk");
    await user.click(screen.getByRole("button", { name: /^test$/i }));

    const nota = await screen.findByRole("status");
    expect(nota).toHaveTextContent(/CHIMERA_ALLOWED_ORIGINS=/);
    expect(nota).toHaveTextContent(window.location.origin);
  });

  it("separates a refused token from an unreachable host", async () => {
    vi.mocked(handshake).mockResolvedValue({ ok: false, reason: "unauthorized" });
    render(<Servers />);
    const user = await preencher("https://chimera.exemplo.com", "errado");
    await user.click(screen.getByRole("button", { name: /^test$/i }));

    expect(await screen.findByRole("status")).toHaveTextContent(/token was refused/i);
  });

  it("does not add a server that failed the handshake", async () => {
    vi.mocked(handshake).mockResolvedValue({ ok: false, reason: "unauthorized" });
    render(<Servers />);
    const user = await preencher("https://chimera.exemplo.com", "errado");
    await user.click(screen.getByRole("button", { name: /^add$/i }));

    await screen.findByRole("status");
    expect(screen.queryByText("VPS")).not.toBeInTheDocument();
    expect(localStorage.getItem("chimera.servers")).toBeNull();
  });

  it("gives the token field a name a screen reader can read on its own", async () => {
    // The hint is a description, not part of the name. Inside the label it would announce as
    // "Token That instance's CHIMERA_SERVER_TOKEN…" — the same defect found in the agent registry.
    render(<Servers />);
    await preencher("https://chimera.exemplo.com", "tk");
    const campo = screen.getByLabelText(/^token$/i);
    expect(campo).toHaveAccessibleName("Token");
    expect(campo).toHaveAccessibleDescription(/CHIMERA_SERVER_TOKEN/);
  });
});
