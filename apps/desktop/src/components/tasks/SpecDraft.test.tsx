import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { SpecDraft } from "@/components/tasks/SpecDraft";
import { draftSpec, writeSpec } from "@/lib/api";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", () => ({
  draftSpec: vi.fn(),
  writeSpec: vi.fn(),
}));

function req(over: Record<string, unknown> = {}) {
  return {
    id: "mostra-o-nome",
    text: "A página mostra o nome da padaria.",
    check: "contains",
    target: "Padaria Aurora",
    required: true,
    ...over,
  };
}

function draft(over: Record<string, unknown> = {}) {
  return {
    name: "padaria-aurora",
    requirements: [req(), req({ id: "sem-lorem", text: "Nada de enchimento.", check: "absent", target: "lorem" })],
    refused_commands: 0,
    refused_ids: [],
    note: "",
    ...over,
  };
}

/**
 * Describing a project instead of writing its YAML.
 *
 * The orchestrator behind this screen plans, works one card at a time and verifies each
 * requirement mechanically — and its only door was a text field asking for the path of a spec
 * file. Everyone who cannot write that YAML was standing outside it.
 */
describe("drafting a spec from a description", () => {
  beforeEach(() => {
    vi.mocked(draftSpec).mockReset().mockResolvedValue(draft() as never);
    vi.mocked(writeSpec).mockReset().mockResolvedValue({ path: "/ws/padaria-aurora.spec.yaml" } as never);
  });

  it("turns a sentence into requirements a person can read", async () => {
    renderWithProviders(<SpecDraft workspace="/ws" onStarted={vi.fn()} />);

    await userEvent.type(screen.getByLabelText(/describe/i), "um site pra minha padaria");
    await userEvent.click(screen.getByRole("button", { name: /draft/i }));

    expect(await screen.findByText("A página mostra o nome da padaria.")).toBeTruthy();
  });

  it("shows the check next to the sentence, not instead of it", async () => {
    // The whole risk of drafting somebody's acceptance authority is a sentence that does not
    // describe its check. Showing only the friendly line would hide exactly the thing that has to
    // be reviewed; showing only the regex would be the YAML field again with extra steps.
    renderWithProviders(<SpecDraft workspace="/ws" onStarted={vi.fn()} />);
    await userEvent.type(screen.getByLabelText(/describe/i), "x");
    await userEvent.click(screen.getByRole("button", { name: /draft/i }));

    await screen.findByText("A página mostra o nome da padaria.");
    expect(screen.getByText("Padaria Aurora")).toBeTruthy();
    expect(screen.getByText(/looks for/i)).toBeTruthy();
    // And the jargon is translated: "contains" is not a word this screen's reader knows.
    expect(screen.queryByText("contains")).toBeNull();
  });

  it("writes nothing until somebody asks it to", async () => {
    renderWithProviders(<SpecDraft workspace="/ws" onStarted={vi.fn()} />);
    await userEvent.type(screen.getByLabelText(/describe/i), "x");
    await userEvent.click(screen.getByRole("button", { name: /draft/i }));

    await screen.findByText(/judged on/i);
    expect(writeSpec).not.toHaveBeenCalled();
  });

  it("writes what was kept, not what was drafted", async () => {
    // The review is only real if deleting a line changes the file. Found the shape of this test
    // the hard way earlier this session: a component can pass every test of its own function and
    // still be wired to send the original payload.
    renderWithProviders(<SpecDraft workspace="/ws" onStarted={vi.fn()} />);
    await userEvent.type(screen.getByLabelText(/describe/i), "x");
    await userEvent.click(screen.getByRole("button", { name: /draft/i }));
    await screen.findByText(/judged on/i);

    await userEvent.click(screen.getByLabelText(/remove sem-lorem/i));
    await userEvent.click(screen.getByRole("button", { name: /create and start/i }));

    await waitFor(() => expect(writeSpec).toHaveBeenCalled());
    const sent = vi.mocked(writeSpec).mock.calls[0][0];
    expect(sent.requirements.map((r) => r.id)).toEqual(["mostra-o-nome"]);
  });

  it("starts the project against the file it just wrote", async () => {
    const onStarted = vi.fn();
    renderWithProviders(<SpecDraft workspace="/ws" onStarted={onStarted} />);
    await userEvent.type(screen.getByLabelText(/describe/i), "x");
    await userEvent.click(screen.getByRole("button", { name: /draft/i }));
    await screen.findByText(/judged on/i);
    await userEvent.click(screen.getByRole("button", { name: /create and start/i }));

    await waitFor(() => expect(onStarted).toHaveBeenCalledWith("/ws/padaria-aurora.spec.yaml"));
  });

  it("writes into the same folder the project will judge", async () => {
    // The spec is checked against the files in the project folder. Written to one folder and
    // judged in another, every requirement reports missing forever.
    renderWithProviders(<SpecDraft workspace="/ws" onStarted={vi.fn()} />);
    await userEvent.type(screen.getByLabelText(/describe/i), "x");
    await userEvent.click(screen.getByRole("button", { name: /draft/i }));
    await screen.findByText(/judged on/i);
    await userEvent.click(screen.getByRole("button", { name: /create and start/i }));

    await waitFor(() => expect(writeSpec).toHaveBeenCalled());
    expect(vi.mocked(writeSpec).mock.calls[0][0].workspace).toBe("/ws");
  });

  it("says how many checks were left out for running a shell command", async () => {
    // Measured on real drafts: one in three emitted one. Dropping it quietly would leave the owner
    // believing the spec verifies something it no longer does.
    vi.mocked(draftSpec).mockResolvedValue(
      draft({ refused_commands: 1, refused_ids: ["page-loads"] }) as never,
    );
    renderWithProviders(<SpecDraft workspace="/ws" onStarted={vi.fn()} />);
    await userEvent.type(screen.getByLabelText(/describe/i), "x");
    await userEvent.click(screen.getByRole("button", { name: /draft/i }));

    expect(await screen.findByText(/shell command were left out/i)).toBeTruthy();
  });

  it("stays quiet when nothing was refused", async () => {
    // The control. A permanent notice about shell commands on every draft is noise that teaches
    // people to skip the one line that matters.
    renderWithProviders(<SpecDraft workspace="/ws" onStarted={vi.fn()} />);
    await userEvent.type(screen.getByLabelText(/describe/i), "x");
    await userEvent.click(screen.getByRole("button", { name: /draft/i }));

    await screen.findByText(/judged on/i);
    expect(screen.queryByText(/left out/i)).toBeNull();
  });

  it("shows the reason instead of a review when the draft did not work", async () => {
    vi.mocked(draftSpec).mockResolvedValue(
      draft({ note: "the draft was not valid JSON", requirements: [] }) as never,
    );
    renderWithProviders(<SpecDraft workspace="/ws" onStarted={vi.fn()} />);
    await userEvent.type(screen.getByLabelText(/describe/i), "x");
    await userEvent.click(screen.getByRole("button", { name: /draft/i }));

    expect(await screen.findByText(/not valid JSON/i)).toBeTruthy();
    expect(screen.queryByText(/judged on/i)).toBeNull();
  });

  it("survives a backend that is not there", async () => {
    vi.mocked(draftSpec).mockRejectedValue(new Error("boom"));
    renderWithProviders(<SpecDraft workspace="/ws" onStarted={vi.fn()} />);
    await userEvent.type(screen.getByLabelText(/describe/i), "x");
    await userEvent.click(screen.getByRole("button", { name: /draft/i }));

    expect(await screen.findByText(/could not reach/i)).toBeTruthy();
  });

  it("will not start a project with nothing left to judge", async () => {
    // A spec with no requirements reports done having verified nothing — the orchestrator refuses
    // it, and the screen should say so before spending the round trip that earns the refusal.
    vi.mocked(draftSpec).mockResolvedValue(draft({ requirements: [req()] }) as never);
    renderWithProviders(<SpecDraft workspace="/ws" onStarted={vi.fn()} />);
    await userEvent.type(screen.getByLabelText(/describe/i), "x");
    await userEvent.click(screen.getByRole("button", { name: /draft/i }));
    await screen.findByText(/judged on/i);

    await userEvent.click(screen.getByLabelText(/remove mostra-o-nome/i));

    expect(screen.getByText(/at least one line/i)).toBeTruthy();
    expect(screen.getByRole("button", { name: /create and start/i })).toHaveProperty("disabled", true);
  });

  it("does not offer to draft an empty description", async () => {
    renderWithProviders(<SpecDraft workspace="/ws" onStarted={vi.fn()} />);
    expect(screen.getByRole("button", { name: /draft/i })).toHaveProperty("disabled", true);
  });
});
