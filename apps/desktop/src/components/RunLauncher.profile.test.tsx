import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { Runs } from "@/components/Runs";
import { getRuns, streamRun } from "@/lib/api";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", async () => (await import("@/test/code-api-mock")).makeCodeApiMock());

/**
 * A default the app applied must not be recorded as a decision somebody made.
 *
 * `worth.py` groups delegation receipts by `(profile, profile_source)` precisely so a run somebody
 * chose "max" for and a run that got the default because there is no picker are not counted as the
 * same evidence. No screen in this app has a profile picker — and none of them sent
 * `profile_source`, so the server's default, `"user"`, applied. Every app run entered the one view
 * built to compare configurations labelled as a deliberate choice.
 */
describe("starting a run", () => {
  beforeEach(() => {
    vi.mocked(getRuns).mockResolvedValue([]);
    vi.mocked(streamRun).mockImplementation(async () => {});
  });

  it("says the profile came from the system, because no screen asks", async () => {
    const user = userEvent.setup();
    renderWithProviders(<Runs workspace="/repo" />);

    await user.type(await screen.findByLabelText(/task/i), "fix the loader");
    await user.click(screen.getByRole("button", { name: /^run$/i }));

    expect(vi.mocked(streamRun).mock.calls[0][0]).toMatchObject({
      profile_source: "system",
    });
  });
});
