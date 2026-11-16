import { screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { RoutingTable, routingModelText } from "@/components/RoutingTable";
import { getRouting } from "@/lib/api";
import type { Routing, RoutingRow } from "@/lib/types";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", () => ({ getRouting: vi.fn() }));

function row(over: Partial<RoutingRow>): RoutingRow {
  return {
    role: "default",
    model: "openrouter/test/dflt",
    effort: null,
    origin: "default",
    effort_origin: "default",
    via: "the product's default",
    via_kind: "product_default",
    inherits: "",
    kind: "chat",
    effort_sent: null,
    local_file: false,
    members: [],
    ...over,
  };
}

function answer(over: Partial<Routing>): Routing {
  return { rows: [], ladder_source: "preset", cost_mode: "auto", denied: "", ...over };
}

describe("RoutingTable", () => {
  beforeEach(() => vi.mocked(getRouting).mockReset());

  it("lists each role with its model, origin and the role it follows", async () => {
    vi.mocked(getRouting).mockResolvedValue(
      answer({
        rows: [
          row({ origin: "env", effort: "high", effort_sent: false, model: "ollama_chat/local" }),
          row({ role: "code.edit", inherits: "default", via: "not differentiated today" }),
          row({ role: "review", model: "", via: "chosen at review time" }),
        ],
      }),
    );
    renderWithProviders(<RoutingTable />);
    await waitFor(() => expect(screen.getByRole("table")).toBeInTheDocument());
    expect(screen.getByText("ollama_chat/local")).toBeInTheDocument();
    expect(screen.getByText("high (effort not sent on this route)")).toBeInTheDocument();
    expect(screen.getByText("environment")).toBeInTheDocument();
    expect(screen.getByText("follows default")).toBeInTheDocument();
    expect(screen.getByText("chosen when used")).toBeInTheDocument();
  });

  it("says where a model came from in the interface language, with the technical sentence as the tooltip", async () => {
    vi.mocked(getRouting).mockResolvedValue(
      answer({
        rows: [
          row({ role: "tier.weak", via: "cost_mode auto preset", via_kind: "preset" }),
          row({ role: "escalation", via: "ladder_of: [weak, default, top]", via_kind: "ladder" }),
          row({ role: "decisions", via: "backend hosted_verbalized default", via_kind: "backend_default" }),
          row({ role: "verified", via: "something the table has no category for", via_kind: "other" }),
        ],
      }),
    );
    renderWithProviders(<RoutingTable />);
    await waitFor(() => expect(screen.getByRole("table")).toBeInTheDocument());
    const preset = screen.getByText("cost preset");
    expect(preset).toHaveAttribute("title", "cost_mode auto preset");
    expect(screen.getByText("from the model ladder")).toHaveAttribute("title", "ladder_of: [weak, default, top]");
    expect(screen.getByText("product default")).toHaveAttribute("title", "backend hosted_verbalized default");
    // An unknown category falls back to the text itself rather than to nothing.
    expect(screen.getByText("something the table has no category for")).toBeInTheDocument();
    // The engineering sentences are no longer the cell's text.
    expect(screen.queryByText("cost_mode auto preset")).toBeNull();
    expect(screen.queryByText("ladder_of: [weak, default, top]")).toBeNull();
  });

  it("says what the policy did instead of an empty table", async () => {
    vi.mocked(getRouting).mockResolvedValue(answer({ denied: "denied by the deployment policy" }));
    renderWithProviders(<RoutingTable />);
    expect(await screen.findByRole("alert")).toHaveTextContent("denied by the deployment policy");
    expect(screen.queryByRole("table")).toBeNull();
  });

  it("names a local file and nothing of its folder", () => {
    const text = routingModelText(row({ role: "chat_voice", model: "voz.onnx", local_file: true }), (k) =>
      k === "settings.routing.localFile" ? "local file" : k,
    );
    expect(text).toBe("voz.onnx (local file)");
  });

  it("shows a list role as its members", () => {
    const text = routingModelText(
      row({ kind: "list", members: [{ model: "a/x", effort: null, effort_sent: null, local_file: false }, { model: "b/y", effort: null, effort_sent: null, local_file: false }] }),
      (k) => k,
    );
    expect(text).toBe("a/x, b/y");
  });
});
