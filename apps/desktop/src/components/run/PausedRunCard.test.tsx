import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi, beforeEach } from "vitest";

import { PausedRunCard } from "@/components/run/PausedRunCard";
import { I18nProvider } from "@/lib/i18n";
import { respondRun } from "@/lib/api";

vi.mock("@/lib/api", () => ({ respondRun: vi.fn() }));

const paused = { thread_id: "run-1", answer: "the untrusted answer", tainted: true };

/** The card reads its wording from context, like every other screen. */
function show(onResolved: (t: string, a: never, r: boolean) => void = () => {}) {
  return render(
    <I18nProvider>
      <PausedRunCard run={paused} onResolved={onResolved as never} />
    </I18nProvider>,
  );
}

beforeEach(() => {
  vi.mocked(respondRun).mockResolvedValue({ ok: true, resume_required: true, retries: false });
});

describe("answering a paused run", () => {
  it("shows what would be finalized, not just that something is waiting", () => {
    show();
    // The decision is about a specific output. A card that says "a run is paused" without showing
    // the answer asks for approval of something the person cannot see.
    expect(screen.getByText("the untrusted answer")).toBeInTheDocument();
  });

  it("accepts the model's own answer without sending an edit", async () => {
    const user = userEvent.setup();
    show();
    await user.click(screen.getByRole("button", { name: "Accept" }));

    expect(respondRun).toHaveBeenCalledWith("run-1", "accept", {
      answer: undefined,
      feedback: undefined,
    });
  });

  it("sends the human's correction, not the model's text, on an edit", async () => {
    const user = userEvent.setup();
    show();
    await user.click(screen.getByRole("button", { name: "Accept an edited answer" }));

    const box = screen.getByLabelText("What it would finalize");
    await user.clear(box);
    await user.type(box, "the corrected answer");
    await user.click(screen.getByRole("button", { name: "Accept" }));

    expect(respondRun).toHaveBeenCalledWith(
      "run-1",
      "edit",
      expect.objectContaining({ answer: "the corrected answer" }),
    );
  });

  it("will not send guidance that is empty — respond costs an attempt", async () => {
    const user = userEvent.setup();
    show();
    const button = screen.getByRole("button", { name: "Send guidance and try again" });
    expect(button).toBeDisabled();

    await user.type(screen.getByLabelText("What should it do differently?"), "cite your source");
    await user.click(button);
    expect(respondRun).toHaveBeenCalledWith(
      "run-1",
      "respond",
      expect.objectContaining({ feedback: "cite your source" }),
    );
  });

  it("hands the resume back to the caller — the verdict alone concludes nothing", async () => {
    const user = userEvent.setup();
    const onResolved = vi.fn();
    show(onResolved);
    await user.click(screen.getByRole("button", { name: "Reject" }));

    await waitFor(() => expect(onResolved).toHaveBeenCalledWith("run-1", "ignore", false));
  });

  it("does not claim a verdict the backend refused", async () => {
    vi.mocked(respondRun).mockResolvedValue({ ok: false, resume_required: false, retries: false });
    const user = userEvent.setup();
    const onResolved = vi.fn();
    show(onResolved);
    await user.click(screen.getByRole("button", { name: "Accept" }));

    // A stale click on a run that already resolved comes back {ok:false}. Resuming anyway would
    // start a run on a thread that no longer has a checkpoint.
    await waitFor(() => expect(respondRun).toHaveBeenCalled());
    expect(onResolved).not.toHaveBeenCalled();
  });
});
