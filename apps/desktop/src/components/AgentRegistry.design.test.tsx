import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AgentRegistry } from "@/components/AgentRegistry";
import { designAgent, getAgentRegistry, putAgent } from "@/lib/api";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", () => ({
  getAgentRegistry: vi.fn(),
  putAgent: vi.fn(),
  deleteAgent: vi.fn(),
  designAgent: vi.fn(),
}));

const DESIGN = {
  id: "revisor-de-textos",
  name: "revisor de textos",
  instructions: "You review marketing copy and say what is weak.",
  allowed_tools: ["read_document", "edit_file"],
  note: "",
};

/**
 * Describing a subagent instead of filling its form from an empty page.
 *
 * `chimera meta` could always design one from a sentence, and printed it into a table — designed,
 * shown, and thrown away every single time. The proposal now lands in the registry form, which is
 * the surface that already edits an agent: reviewing a design and editing an agent are the same
 * act, and a second surface would be a second thing to keep in step with the first.
 *
 * The review is not a formality. Measured on three real descriptions, an agent asked only to *say
 * what is weak about a marketing text* came back holding `edit_file` — a tool that rewrites files.
 * Over-granting is the tendency, and the person reading the list is the one who knows the agent was
 * never meant to touch anything.
 */
describe("describing an agent", () => {
  beforeEach(() => {
    vi.mocked(getAgentRegistry).mockReset().mockResolvedValue([] as never);
    vi.mocked(designAgent).mockReset().mockResolvedValue(DESIGN as never);
    vi.mocked(putAgent).mockReset().mockResolvedValue([] as never);
  });

  async function describeOne(text = "um agente que revisa meus textos") {
    renderWithProviders(<AgentRegistry />);
    await userEvent.click(await screen.findByRole("button", { name: /describe one/i }));
    await userEvent.type(screen.getByLabelText(/describe one/i), text);
    await userEvent.click(screen.getByRole("button", { name: /design it/i }));
  }

  it("fills the form somebody was going to fill anyway", async () => {
    await describeOne();

    expect(await screen.findByDisplayValue("revisor de textos")).toBeTruthy();
    expect(screen.getByDisplayValue(/say what is weak/i)).toBeTruthy();
  });

  it("saves nothing until somebody says so", async () => {
    // A design is a proposal. Writing it into the registry on arrival would make the review a
    // formality performed after the fact.
    await describeOne();

    await screen.findByDisplayValue("revisor de textos");
    expect(putAgent).not.toHaveBeenCalled();
  });

  it("warns that the tools are chosen generously", async () => {
    // The measured tendency, said before the save rather than discovered after it.
    await describeOne();

    expect(await screen.findByText(/check the tools before saving/i)).toBeTruthy();
  });

  it("says what an empty tool list actually means", async () => {
    // The inversion that once let a subagent run outside its owner's denylist:
    // `AgentDef.allowed_tools` reads empty as NO RESTRICTION, the opposite of `Role`. A design that
    // named nothing must not arrive looking like a locked-down agent.
    vi.mocked(designAgent).mockResolvedValue({
      ...DESIGN,
      allowed_tools: [],
      note: "no tools were chosen — an empty list lets it use every tool",
    } as never);
    await describeOne();

    expect(await screen.findByText(/lets it use every tool/i)).toBeTruthy();
  });

  it("shows the reason instead of a form when nothing could be designed", async () => {
    vi.mocked(designAgent).mockResolvedValue({
      id: "",
      name: "",
      instructions: "",
      allowed_tools: [],
      note: "no usable agent could be read from that description",
    } as never);
    await describeOne();

    expect(await screen.findByText(/no usable agent/i)).toBeTruthy();
    expect(screen.queryByDisplayValue("revisor de textos")).toBeNull();
  });

  it("survives a backend that is not there", async () => {
    vi.mocked(designAgent).mockRejectedValue(new Error("network error"));
    await describeOne();

    expect(await screen.findByText(/network error/i)).toBeTruthy();
  });

  it("does not offer to design an empty description", async () => {
    renderWithProviders(<AgentRegistry />);
    await userEvent.click(await screen.findByRole("button", { name: /describe one/i }));

    expect(screen.getByRole("button", { name: /design it/i })).toHaveProperty("disabled", true);
  });

  it("keeps the plain New button working", async () => {
    // The described path is an addition. Somebody who knows exactly what they want should not have
    // to describe it to a model first.
    renderWithProviders(<AgentRegistry />);
    await userEvent.click(await screen.findByRole("button", { name: /add|new/i }));

    await waitFor(() => expect(designAgent).not.toHaveBeenCalled());
  });
});
