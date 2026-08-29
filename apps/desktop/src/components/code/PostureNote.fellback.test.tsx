/** Why the shell would run on this machine — and the sentence that says which reason it was.
 *
 * There was one sentence: *"A container was configured, but none is running — this is your
 * machine."* It was written when a container was the only boundary there was, and it was true then.
 *
 * Once the default sandbox became `auto`, the same line started appearing on machines where nobody
 * configured a container and no daemon would have helped — Windows, where the mechanism is a
 * restricted token plus network filters and the app deliberately does not attempt it, and any Linux
 * that refuses unprivileged user namespaces. The conclusion stayed right and the REASON became
 * invented, which is worse than vague: it sends someone to start Docker to fix something Docker
 * does not fix.
 *
 * Found by installing the release on Windows and turning commands on.
 */

import { screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { PostureNote } from "@/components/code/PostureNote";
import { getDoctor, getPostureFacts } from "@/lib/api";
import { postureFacts as facts } from "@/test/code-api-mock";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", () => ({ getDoctor: vi.fn(), getPostureFacts: vi.fn() }));

const mockFacts = vi.mocked(getPostureFacts);

describe("PostureNote — why the shell is on this machine", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    // Left as a bare `vi.fn()` this resolves to `undefined`, react-query treats that as a failed
    // query, and the component renders its error line instead of any sentence — so every assertion
    // below would fail for a reason that has nothing to do with what is being tested.
    vi.mocked(getDoctor).mockResolvedValue({ providers: [] } as never);
  });

  it("names the missing OS sandbox when there is no container in the story", async () => {
    mockFacts.mockResolvedValue(
      facts({ fell_back_to_host: true, fell_back_reason: "no_os_sandbox" }) as never,
    );

    renderWithProviders(<PostureNote workspace="/repo" reach="workspace_shell" approval="never" />);

    expect(await screen.findByText(/no OS sandbox is available/i)).toBeInTheDocument();
    expect(screen.queryByText(/container was configured/i)).not.toBeInTheDocument();
  });

  it("still names the container when a container really was configured", async () => {
    // The original case did not go away and its advice works: start the daemon.
    mockFacts.mockResolvedValue(
      facts({ fell_back_to_host: true, fell_back_reason: "no_container" }) as never,
    );

    renderWithProviders(<PostureNote workspace="/repo" reach="workspace_shell" approval="never" />);

    expect(await screen.findByText(/container was configured/i)).toBeInTheDocument();
    expect(screen.queryByText(/no OS sandbox is available/i)).not.toBeInTheDocument();
  });

  it("says nothing about a fall-back when there was none", async () => {
    mockFacts.mockResolvedValue(facts({ shell: "isolated" }) );

    renderWithProviders(<PostureNote workspace="/repo" reach="workspace_shell" approval="never" />);

    await screen.findByText(/repo/i);
    expect(screen.queryByText(/container was configured/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/no OS sandbox is available/i)).not.toBeInTheDocument();
  });

  it("falls back to the container sentence when the reason is missing", async () => {
    // A server that predates `fell_back_reason` sends the flag without it. Reading that as "no
    // sandbox exists here" would be a guess; the container sentence is what those servers meant.
    mockFacts.mockResolvedValue(facts({ fell_back_to_host: true }) );

    renderWithProviders(<PostureNote workspace="/repo" reach="workspace_shell" approval="never" />);

    expect(await screen.findByText(/container was configured/i)).toBeInTheDocument();
  });
});
