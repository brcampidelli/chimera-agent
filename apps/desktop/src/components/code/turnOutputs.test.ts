import { describe, expect, it } from "vitest";
import { MAX_OUTPUTS, turnOutputs } from "@/components/code/turnOutputs";
import type { CodeToolEvent } from "@/lib/api";

const call = (name: string, args: Record<string, string>, ok = true): CodeToolEvent => ({
  name,
  arguments: args,
  ok,
  observation: "",
});

describe("turnOutputs — what a receipt offers to open beside", () => {
  it("names the deliverables each writer produced, in the order written", () => {
    const tools = [
      call("create_document", { path: "out/report.docx" }),
      call("write_file", { path: "index.html" }),
      call("render_chart", { out: "sales.png", format: "png" }),
      call("generate_image", { out: "cover.webp" }),
    ];
    expect(turnOutputs(tools)).toEqual(["out/report.docx", "index.html", "sales.png", "cover.webp"]);
  });

  it("uses the tool's own default name when the path was left out", () => {
    // `render_chart` writes chart.<format> and `generate_image` generated_image.png without one —
    // the file exists, so the receipt has to be able to name it.
    const tools = [call("render_chart", { format: "SVG" }), call("generate_image", {})];
    expect(turnOutputs(tools)).toEqual(["chart.svg", "generated_image.png"]);
  });

  it("offers nothing a failed or refused call claimed to write", () => {
    expect(turnOutputs([call("create_document", { path: "report.pdf" }, false)])).toEqual([]);
  });

  it("leaves out files the viewer has nothing to show for, and readers", () => {
    const tools = [
      call("write_file", { path: "src/app.py" }),
      call("write_file", { path: "notes.txt" }),
      call("read_file", { path: "page.html" }),
    ];
    expect(turnOutputs(tools)).toEqual([]);
  });

  it("adds an openable file a live edit frame names, once", () => {
    const tools = [call("write_file", { path: "site/index.html" })];
    const edits = [{ path: "site/index.html" }, { path: "site/about.htm" }, { path: "app.ts" }];
    expect(turnOutputs(tools, edits)).toEqual(["site/index.html", "site/about.htm"]);
  });

  it("stops at a handful, so a turn that wrote forty images is read in the tree", () => {
    const tools = Array.from({ length: 40 }, (_, i) => call("generate_image", { out: `img${i}.png` }));
    expect(turnOutputs(tools)).toHaveLength(MAX_OUTPUTS);
  });
});
