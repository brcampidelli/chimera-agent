import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { PreviewPane, sharesAppOrigin } from "@/components/code/PreviewPane";
import {
  capturePreview,
  closePreviewSelect,
  discardPreviewAttachments,
  getDevServerLog,
  listDevServers,
  pickPreviewElement,
  startDevServer,
  stopDevServer,
  type DevServers,
} from "@/lib/api";
import { ADD_ATTACHMENT_EVENT, useAddAttachmentRequest } from "@/lib/attach-request";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", () => ({
  listDevServers: vi.fn(),
  startDevServer: vi.fn(),
  stopDevServer: vi.fn(),
  getDevServerLog: vi.fn(),
  capturePreview: vi.fn(),
  pickPreviewElement: vi.fn(),
  closePreviewSelect: vi.fn(),
  discardPreviewAttachments: vi.fn(),
}));

function listing(over: Partial<DevServers> = {}): DevServers {
  return {
    found: true,
    source: ".chimera/launch.json",
    errors: [],
    warnings: [],
    auto_verify: true,
    auto_verify_setting: true,
    can_start: true,
    cannot_start_reason: "",
    config_changed: [],
    servers: [
      { name: "web", state: "stopped", port: 3000, kind: "process", pid: null, url: null, exit_code: null, command: 'NODE_ENV=development PORT=3000 npm run dev', cwd: "web", limit_hours: null },
    ],
    ...over,
  };
}

beforeEach(() => {
  vi.mocked(listDevServers).mockReset();
  vi.mocked(startDevServer).mockReset().mockResolvedValue({ ok: true, message: "port 3000" });
  vi.mocked(stopDevServer).mockReset().mockResolvedValue({ ok: true, message: "stopped" });
  vi.mocked(getDevServerLog).mockReset().mockResolvedValue({ lines: ["$ npm run dev", "ready"] });
  vi.mocked(capturePreview).mockReset().mockResolvedValue({ image: "AAAA", width: 1280, height: 720, url: "http://localhost:3123/" });
  vi.mocked(pickPreviewElement).mockReset().mockResolvedValue({
    label: "<button.primary> 'Salvar'",
    selector: "button.primary",
    tag: "button",
    text: "Salvar",
    attrs: {},
    box: [10, 10, 100, 40],
    attachment: { id: "doc1", name: "doc1.md", kind: "document", chars: 120, note: "" },
    crop: { id: "img1", name: "img1.png", kind: "image", chars: 0, note: "" },
  });
  vi.mocked(closePreviewSelect).mockReset().mockResolvedValue({ ok: true, message: "closed" });
  vi.mocked(discardPreviewAttachments).mockReset().mockResolvedValue({ ok: true, message: "2 removed" });
});

/** A conversation screen's ear: without one mounted, a picked element has nowhere to go. */
function Tray() {
  useAddAttachmentRequest(() => undefined);
  return null;
}

function runningListing(): DevServers {
  return listing({
    servers: [
      { name: "web", state: "running", port: 3123, kind: "process", pid: 7, url: "http://localhost:3123", exit_code: null, command: "npm run dev", cwd: ".", limit_hours: null },
    ],
  });
}

describe("PreviewPane", () => {
  it("lists the configured servers and starts one by name", async () => {
    vi.mocked(listDevServers).mockResolvedValue(listing());
    renderWithProviders(<PreviewPane workspace="/w" onClose={() => {}} />);
    const row = await screen.findByTestId("dev-server-web");
    expect(row).toHaveTextContent("port 3000");
    expect(row).toHaveTextContent("stopped");
    await userEvent.click(row.querySelector("button")!);
    expect(startDevServer).toHaveBeenCalledWith("/w", "web");
  });

  it("frames the running server's page, sandboxed, and shows its output", async () => {
    vi.mocked(listDevServers).mockResolvedValue(
      listing({
        servers: [
          { name: "web", state: "running", port: 3123, kind: "process", pid: 7, url: "http://localhost:3123", exit_code: null, command: "npm run dev", cwd: ".", limit_hours: null },
        ],
      }),
    );
    renderWithProviders(<PreviewPane workspace="/w" onClose={() => {}} />);
    // Listening is not asking: nothing is framed until the person clicks Show.
    await screen.findByTestId("dev-server-web");
    expect(screen.queryByTitle("Preview of web")).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Show" }));
    const frame = await screen.findByTitle("Preview of web");
    expect(frame).toHaveAttribute("src", "http://localhost:3123");
    expect(frame.getAttribute("sandbox")).toContain("allow-scripts");
    // Opaque origin: cookies are shared across ports of a host.
    expect(frame.getAttribute("sandbox")).not.toContain("allow-same-origin");
    expect(screen.getByRole("link", { name: "Open in browser" })).toHaveAttribute("href", "http://localhost:3123");
    await userEvent.click(screen.getByText("Output"));
    await waitFor(() => expect(screen.getByText(/ready/)).toBeInTheDocument());
    await userEvent.click(screen.getByRole("button", { name: "Stop" }));
    expect(stopDevServer).toHaveBeenCalledWith("/w", "web");
  });

  it("never frames a page that shares the app's own address", () => {
    expect(sharesAppOrigin("http://localhost:3000", { port: "3000" })).toBe(true);
    expect(sharesAppOrigin("http://127.0.0.1:3000", { port: "3000" })).toBe(true); // another name, same port
    expect(sharesAppOrigin("http://localhost:3001", { port: "3000" })).toBe(false);
    expect(sharesAppOrigin("not a url", { port: "3000" })).toBe(true);
  });

  it("says there is no launch.json, and says why a server cannot start", async () => {
    vi.mocked(listDevServers).mockResolvedValue(listing({ found: false, servers: [] }));
    const { unmount } = renderWithProviders(<PreviewPane workspace="/w" onClose={() => {}} />);
    expect(await screen.findByText(/has no launch.json/)).toBeInTheDocument();
    unmount();
    vi.mocked(listDevServers).mockResolvedValue(listing({ can_start: false, cannot_start_reason: "commands are off" }));
    renderWithProviders(<PreviewPane workspace="/w" onClose={() => {}} />);
    expect(await screen.findByText(/Commands are off for this folder/)).toBeInTheDocument();
    expect(await screen.findByRole("button", { name: "Start" })).toBeDisabled();
  });

  it("shows the whole command with its env and folder, and warns when the config files changed", async () => {
    vi.mocked(listDevServers).mockResolvedValue(listing({ config_changed: ["package.json", "vite.config.ts"] }));
    renderWithProviders(<PreviewPane workspace="/w" onClose={() => {}} />);
    const command = await screen.findByTestId("dev-server-command-web");
    expect(command).toHaveTextContent("NODE_ENV=development PORT=3000 npm run dev");
    expect(command).toHaveTextContent("in web");
    expect(screen.getByRole("alert")).toHaveTextContent("package.json, vite.config.ts");
  });

  it("says a server ended by the time limit is that, not a silent exit", async () => {
    vi.mocked(listDevServers).mockResolvedValue(
      listing({
        servers: [
          { name: "web", state: "timed_out", port: 3000, kind: "process", pid: null, url: null, exit_code: null, command: "npm run dev", cwd: ".", limit_hours: 6 },
        ],
      }),
    );
    renderWithProviders(<PreviewPane workspace="/w" onClose={() => {}} />);
    expect(await screen.findByText("ended (limit of 6 h)")).toBeInTheDocument();
  });

  it("shows the file's faults and its secret warnings", async () => {
    vi.mocked(listDevServers).mockResolvedValue(
      listing({ errors: ["configurations.0: port must be positive"], warnings: ["web: env KEY looks like a secret"], servers: [] }),
    );
    renderWithProviders(<PreviewPane workspace="/w" onClose={() => {}} />);
    expect(await screen.findByText(/launch.json is invalid/)).toBeInTheDocument();
    expect(screen.getByText(/port must be positive/)).toBeInTheDocument();
    expect(screen.getByText(/looks like a secret/)).toBeInTheDocument();
  });

  describe("selecting an element", () => {
    it("shows a picture, sends the click in the page's own pixels, and adds the chips", async () => {
      vi.mocked(listDevServers).mockResolvedValue(runningListing());
      const added: unknown[] = [];
      const on = (e: Event) => added.push((e as CustomEvent).detail);
      window.addEventListener(ADD_ATTACHMENT_EVENT, on);
      renderWithProviders(
        <>
          <Tray />
          <PreviewPane workspace="/w" onClose={() => {}} />
        </>,
      );
      await userEvent.click(await screen.findByRole("button", { name: "Show" }));
      await userEvent.click(await screen.findByRole("button", { name: "Select element" }));
      expect(capturePreview).toHaveBeenCalledWith("/w", "web");
      const picture = await screen.findByRole("img", { name: /Picture of the page of web/ });
      // The image is shown at half the page's width: a click at (50, 25) of it is (100, 50) of the page.
      vi.spyOn(picture, "getBoundingClientRect").mockReturnValue({ left: 0, top: 0, width: 640, height: 360, right: 640, bottom: 360, x: 0, y: 0, toJSON: () => ({}) });
      await userEvent.pointer({ keys: "[MouseLeft]", target: picture, coords: { clientX: 50, clientY: 25 } });
      await waitFor(() => expect(pickPreviewElement).toHaveBeenCalledWith("/w", "web", 100, 50));
      await waitFor(() => expect(added).toHaveLength(1));
      window.removeEventListener(ADD_ATTACHMENT_EVENT, on);
      const chips = added[0] as { id: string; name: string }[];
      expect(chips.map((c) => c.id)).toEqual(["doc1", "img1"]);
      expect(chips[0].name).toBe("Element: <button.primary> 'Salvar'");
      expect(chips[1].name).toBe("Crop: <button.primary> 'Salvar'");
      // Back on the frame, and the server's browser is closed.
      expect(await screen.findByTitle("Preview of web")).toBeInTheDocument();
      expect(closePreviewSelect).toHaveBeenCalled();
    });

    it("says nothing was added, and deletes what it stored, when no conversation is open to take it", async () => {
      vi.mocked(listDevServers).mockResolvedValue(runningListing());
      renderWithProviders(<PreviewPane workspace="/w" onClose={() => {}} />);
      await userEvent.click(await screen.findByRole("button", { name: "Show" }));
      await userEvent.click(await screen.findByRole("button", { name: "Select element" }));
      const picture = await screen.findByRole("img", { name: /Picture of the page of web/ });
      vi.spyOn(picture, "getBoundingClientRect").mockReturnValue({ left: 0, top: 0, width: 1280, height: 720, right: 1280, bottom: 720, x: 0, y: 0, toJSON: () => ({}) });
      await userEvent.pointer({ keys: "[MouseLeft]", target: picture, coords: { clientX: 5, clientY: 5 } });
      expect(await screen.findByText(/No conversation is open to receive/)).toBeInTheDocument();
      expect(screen.queryByText(/^Added/)).toBeNull();
      await waitFor(() => expect(discardPreviewAttachments).toHaveBeenCalledWith(["doc1", "img1"]));
    });

    it("takes a new picture when the server refuses a pick because the page changed", async () => {
      vi.mocked(listDevServers).mockResolvedValue(runningListing());
      vi.mocked(pickPreviewElement).mockRejectedValue(new Error("the page changed since the picture was taken; capture it again"));
      renderWithProviders(<PreviewPane workspace="/w" onClose={() => {}} />);
      await userEvent.click(await screen.findByRole("button", { name: "Show" }));
      await userEvent.click(await screen.findByRole("button", { name: "Select element" }));
      const picture = await screen.findByRole("img", { name: /Picture of the page of web/ });
      vi.spyOn(picture, "getBoundingClientRect").mockReturnValue({ left: 0, top: 0, width: 1280, height: 720, right: 1280, bottom: 720, x: 0, y: 0, toJSON: () => ({}) });
      await userEvent.pointer({ keys: "[MouseLeft]", target: picture, coords: { clientX: 5, clientY: 5 } });
      await waitFor(() => expect(capturePreview).toHaveBeenCalledTimes(2));
      expect(await screen.findByRole("alert")).toHaveTextContent("page changed");
    });

    it("starts and stops on Ctrl+Shift+S, and Escape leaves the mode", async () => {
      vi.mocked(listDevServers).mockResolvedValue(runningListing());
      renderWithProviders(<PreviewPane workspace="/w" onClose={() => {}} />);
      await userEvent.click(await screen.findByRole("button", { name: "Show" }));
      await screen.findByTitle("Preview of web");
      await userEvent.keyboard("{Control>}{Shift>}s{/Shift}{/Control}");
      await waitFor(() => expect(capturePreview).toHaveBeenCalledTimes(1));
      expect(await screen.findByText(/Click the element to send/)).toBeInTheDocument();
      await userEvent.keyboard("{Escape}");
      expect(await screen.findByTitle("Preview of web")).toBeInTheDocument();
      expect(closePreviewSelect).toHaveBeenCalled();
    });

    it("offers nothing for a server that was not started by this app, and nothing before one is shown", async () => {
      vi.mocked(listDevServers).mockResolvedValue(
        listing({
          servers: [
            { name: "ext", state: "running", port: 3999, kind: "attach", pid: null, url: "http://localhost:3999", exit_code: null, command: "", cwd: ".", limit_hours: null },
          ],
        }),
      );
      renderWithProviders(<PreviewPane workspace="/w" onClose={() => {}} />);
      expect(screen.queryByRole("button", { name: "Select element" })).toBeNull();
      await userEvent.click(await screen.findByRole("button", { name: "Show" }));
      await screen.findByTitle("Preview of ext");
      expect(screen.queryByRole("button", { name: "Select element" })).toBeNull();
      await userEvent.keyboard("{Control>}{Shift>}s{/Shift}{/Control}");
      expect(capturePreview).not.toHaveBeenCalled();
    });

    it("says why the page could not be captured", async () => {
      vi.mocked(listDevServers).mockResolvedValue(runningListing());
      vi.mocked(capturePreview).mockRejectedValue(new Error("port is one Chimera itself serves on"));
      renderWithProviders(<PreviewPane workspace="/w" onClose={() => {}} />);
      await userEvent.click(await screen.findByRole("button", { name: "Show" }));
      await userEvent.click(await screen.findByRole("button", { name: "Select element" }));
      expect(await screen.findByRole("alert")).toHaveTextContent("Chimera itself serves on");
    });
  });
});
