import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { PreviewPane, sharesAppOrigin } from "@/components/code/PreviewPane";
import { getDevServerLog, listDevServers, startDevServer, stopDevServer, type DevServers } from "@/lib/api";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", () => ({
  listDevServers: vi.fn(),
  startDevServer: vi.fn(),
  stopDevServer: vi.fn(),
  getDevServerLog: vi.fn(),
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
});

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
});
