import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { RunLauncher } from "@/components/run/RunLauncher";
import { getPausedRuns, getRoleModels, streamRun } from "@/lib/api";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", async () => (await import("@/test/code-api-mock")).makeCodeApiMock());

/**
 * What the user picked has to reach the server.
 *
 * `/api/runs` has accepted `profile` and `roles` since roles existed, and `app.py` routes plan,
 * edit and review off them. This screen sent neither — its own comment said so: *"No profile picker
 * on this screen either, so the receipt must not say a person picked one."* Honest bookkeeping
 * around a missing control, which kept the gap invisible: every run took the built-in tiers, and
 * the receipt agreed that nobody had chosen anything.
 *
 * The `profile_source` assertions are the half worth having. `worth.py` groups evidence by
 * (profile, profile_source) so that a run somebody deliberately set to "max" and a run that merely
 * got the default are never pooled. A form that always claimed "user" would corrupt the one view
 * built to judge whether the profiles differ at all — so the untouched case is tested first.
 */
describe("RunLauncher — the choice reaches the run", () => {
  beforeEach(() => {
    vi.mocked(getPausedRuns).mockResolvedValue([]);
    vi.mocked(getRoleModels).mockResolvedValue({
      explore: "scout-model",
      plan: "planner-model",
      edit: "writer-model",
      review: "critic-model",
      fuse_plan: false,
      fuse_review: false,
    });
    vi.mocked(streamRun).mockResolvedValue(undefined);
  });

  async function launch(task = "arrume o build") {
    const user = userEvent.setup();
    renderWithProviders(<RunLauncher />);
    await user.type(await screen.findByPlaceholderText(/task|tarefa/i), task);
    return user;
  }

  function sent() {
    expect(streamRun).toHaveBeenCalled();
    return vi.mocked(streamRun).mock.calls[0][0];
  }

  it("sends the profile it is showing", async () => {
    const user = await launch();
    await user.click(screen.getByRole("button", { name: /^Run$|Executar/i }));

    await waitFor(() => expect(streamRun).toHaveBeenCalled());
    expect(sent().profile).toBe("balanced");
  });

  it("does not claim a person chose the default", async () => {
    const user = await launch();
    await user.click(screen.getByRole("button", { name: /^Run$|Executar/i }));

    await waitFor(() => expect(streamRun).toHaveBeenCalled());
    expect(sent().profile_source).toBe("system");
  });

  it("records a real choice as one", async () => {
    const user = await launch();
    await user.click(screen.getByRole("button", { name: /^max$/i, pressed: false }));
    await user.click(screen.getByRole("button", { name: /^Run$|Executar/i }));

    await waitFor(() => expect(streamRun).toHaveBeenCalled());
    const req = sent();
    expect(req.profile).toBe("max");
    expect(req.profile_source).toBe("user");
  });

  it("sends no role override when nobody set one", async () => {
    const user = await launch();
    await user.click(screen.getByRole("button", { name: /^Run$|Executar/i }));

    await waitFor(() => expect(streamRun).toHaveBeenCalled());
    // `null`, never `{explore: ""}`: the server merges field by field and reads absence as "keep
    // the profile's answer", so an empty slug would ask it to run that role on a model named "".
    expect(sent().roles).toBeNull();
  });

  it("collapses to one picker when one model should do everything", async () => {
    // The box did nothing at first, and the reason is worth keeping: "one model" was DERIVED from
    // the four values being equal and non-empty, so ticking it before choosing anything wrote four
    // blanks and read them straight back as "not one model" — the box would not even stay ticked.
    // Three intents exist and two share the same four values, so it has to be held, not derived.
    const user = await launch();
    await user.click(screen.getByText(/which model does what|qual modelo faz o quê/i));

    const box = screen.getByRole("checkbox", { name: /one model|um modelo/i });
    await user.click(box);

    expect((box as HTMLInputElement).checked).toBe(true);
    expect(screen.getByText(/every step|todas as etapas/i)).toBeTruthy();
    // And the per-role rows are gone: four pickers showing the same slug would invite editing one
    // of them, which silently leaves the state the box still claims.
    expect(screen.queryByText(/^Explore$|^Explorar$/)).toBeNull();
  });

  it("gives the roles back when it is unticked", async () => {
    const user = await launch();
    await user.click(screen.getByText(/which model does what|qual modelo faz o quê/i));
    const box = screen.getByRole("checkbox", { name: /one model|um modelo/i });

    await user.click(box);
    await user.click(box);

    expect((box as HTMLInputElement).checked).toBe(false);
    expect(screen.getByText(/^Explore$|^Explorar$/)).toBeTruthy();
  });

  it("shows each role's own model as what 'no choice' means there", async () => {
    // Two wrong shapes preceded this, in opposite directions. The first REPLACED the resolved slug
    // with an empty picker, so switching the control on hid the four model names it exists to
    // change. The second showed both — and the picker's own half was the INSTALL default, so
    // `Explore` read "default · deepseek-chat-v3.1" beside the mistral it actually runs on: two
    // model names on one row, the prominent one wrong for that role.
    //
    // One name per row now, inside the chip, and it is the role's. A row wired to the wrong role
    // fails here, which is why all four are asserted rather than one.
    await launch();
    await userEvent.setup().click(screen.getByText(/which model does what|qual modelo faz o quê/i));

    for (const slug of ["scout-model", "planner-model", "writer-model", "critic-model"]) {
      expect(await screen.findByText(new RegExp(slug)), `${slug} is not shown`).toBeTruthy();
    }
  });
});
