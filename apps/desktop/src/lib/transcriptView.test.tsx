import { screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { ToolRow } from "@/components/code/Conversation";
import type { CodeToolEvent } from "@/lib/api";
import { CARD_KINDS } from "@/lib/layout/model";
import {
  getTranscriptView,
  setTranscriptView,
  TRANSCRIPT_VIEW_KEY,
  viewOpensKind,
  viewShowsToolDetail,
} from "@/lib/transcriptView";
import { renderWithProviders } from "@/test/utils";

/**
 * The default transcript view (C8). Each view has to be made of something the conversation can
 * actually show; a view whose only effect is its own label is the defect this file guards against.
 */
afterEach(() => localStorage.clear());

describe("the stored view", () => {
  it("is Normal when nothing, or something unknown, is stored", () => {
    expect(getTranscriptView()).toBe("normal");
    localStorage.setItem(TRANSCRIPT_VIEW_KEY, "verbose");
    expect(getTranscriptView()).toBe("normal");
  });

  it("survives a restart", () => {
    setTranscriptView("reasoning");
    expect(localStorage.getItem(TRANSCRIPT_VIEW_KEY)).toBe("reasoning");
    expect(getTranscriptView()).toBe("reasoning");
  });
});

describe("what each view opens", () => {
  it("Normal opens nothing over the per-kind preferences", () => {
    for (const kind of CARD_KINDS) expect(viewOpensKind("normal", kind)).toBe(false);
    expect(viewShowsToolDetail("normal")).toBe(false);
  });

  it("Steps opens the tool list and nothing else", () => {
    expect(CARD_KINDS.filter((kind) => viewOpensKind("reasoning", kind))).toEqual(["tools"]);
    expect(viewShowsToolDetail("reasoning")).toBe(false);
  });

  it("Detailed opens every kind and the tool detail", () => {
    for (const kind of CARD_KINDS) expect(viewOpensKind("detailed", kind)).toBe(true);
    expect(viewShowsToolDetail("detailed")).toBe(true);
  });
});

describe("a tool row under Detailed", () => {
  const tool: CodeToolEvent = {
    name: "write_file",
    arguments: { path: "src/a.ts", content: "export const a = 1;" },
    ok: true,
    observation: "wrote 19 bytes",
  };

  it("shows only the first argument and keeps a successful output folded, by default", () => {
    const { container } = renderWithProviders(<ToolRow tool={tool} />);
    expect(screen.queryByTestId("tool-arguments")).toBeNull();
    expect(screen.queryByText(/export const a = 1;/)).toBeNull();
    expect(container.querySelector("details")).not.toHaveAttribute("open");
  });

  it("shows every argument and unfolds the output", () => {
    const { container } = renderWithProviders(<ToolRow tool={tool} detailed />);
    const args = screen.getByTestId("tool-arguments");
    expect(args).toHaveTextContent('"content": "export const a = 1;"');
    expect(args).toHaveTextContent('"path": "src/a.ts"');
    expect(container.querySelector("details")).toHaveAttribute("open");
  });
});
