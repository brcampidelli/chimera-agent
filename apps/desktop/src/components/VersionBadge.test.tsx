import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { VersionBadge } from "@/components/VersionBadge";
import { getVersion } from "@/lib/api";
import { renderWithProviders } from "@/test/utils";
import type { VersionInfo } from "@/lib/types";

vi.mock("@/lib/api", () => ({ getVersion: vi.fn() }));

const mockGetVersion = vi.mocked(getVersion);

function version(over: Partial<VersionInfo> = {}): VersionInfo {
  return { version: "0.32.2", latest: null, update_available: false, notes_url: null, ...over };
}

/** The badge's whole job is to signal an update ONLY when one is confirmed. Every test here is a guard
 *  against the two dishonest failure modes: claiming an update that isn't there, and nagging about a
 *  version the user already skipped. */
describe("VersionBadge", () => {
  beforeEach(() => {
    mockGetVersion.mockReset();
  });

  it("shows the running version quietly when no update is available", async () => {
    mockGetVersion.mockResolvedValue(version({ version: "0.32.2" }));
    renderWithProviders(<VersionBadge />);

    expect(await screen.findByText("v0.32.2")).toBeInTheDocument();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it("shows no update signal when the check failed or is offline (latest=null)", async () => {
    // The backend degrades to {latest:null, update_available:false} on any error — never a false pill.
    mockGetVersion.mockResolvedValue(version({ latest: null, update_available: false }));
    renderWithProviders(<VersionBadge />);

    expect(await screen.findByText("v0.32.2")).toBeInTheDocument();
    expect(screen.queryByText(/available/i)).not.toBeInTheDocument();
  });

  it("shows no update signal when latest is KNOWN but not newer (update_available=false)", async () => {
    // The nastiest false-signal case, and the one the other "no signal" tests miss because they use
    // latest=null: the check SUCCEEDED and returned a version, it just isn't newer. `update_available`
    // is the backend's authority — keying the pill off `latest` alone would nag every up-to-date user.
    mockGetVersion.mockResolvedValue(
      version({ version: "0.32.2", latest: "0.32.2", update_available: false, notes_url: null }),
    );
    renderWithProviders(<VersionBadge />);

    expect(await screen.findByText("v0.32.2")).toBeInTheDocument();
    expect(screen.queryByText(/available/i)).not.toBeInTheDocument();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it("renders nothing at all when the version request itself rejects", async () => {
    mockGetVersion.mockRejectedValue(new Error("500 Internal Server Error"));
    const { container } = renderWithProviders(<VersionBadge />);

    await waitFor(() => expect(mockGetVersion).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
  });

  it("shows the update pill when the backend confirms a newer release", async () => {
    mockGetVersion.mockResolvedValue(
      version({ latest: "0.33.0", update_available: true, notes_url: "https://example.test/r/0.33.0" }),
    );
    renderWithProviders(<VersionBadge />);

    expect(await screen.findByRole("button", { name: "v0.33.0 available" })).toBeInTheDocument();
  });

  it("opens a prompt with the release link and the pip command when the pill is clicked", async () => {
    const user = userEvent.setup();
    mockGetVersion.mockResolvedValue(
      version({ latest: "0.33.0", update_available: true, notes_url: "https://example.test/r/0.33.0" }),
    );
    renderWithProviders(<VersionBadge />);

    await user.click(await screen.findByRole("button", { name: "v0.33.0 available" }));

    expect(screen.getByText("A new version (v0.33.0) is available. Update?")).toBeInTheDocument();
    expect(screen.getByText("pip install -U 'chimera-agent[desktop]'")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /View release/ })).toHaveAttribute(
      "href",
      "https://example.test/r/0.33.0",
    );
  });

  it("hides the pill and persists the skipped version when Dismiss is clicked", async () => {
    const user = userEvent.setup();
    mockGetVersion.mockResolvedValue(version({ latest: "0.33.0", update_available: true }));
    renderWithProviders(<VersionBadge />);

    await user.click(await screen.findByRole("button", { name: "v0.33.0 available" }));
    await user.click(screen.getByRole("button", { name: "Dismiss" }));

    expect(screen.queryByRole("button", { name: /available/ })).not.toBeInTheDocument();
    expect(screen.getByText("v0.32.2")).toBeInTheDocument();
    expect(localStorage.getItem("chimera.updateDismissed")).toBe("0.33.0");
  });

  it("does not re-prompt for a version that was already dismissed", async () => {
    localStorage.setItem("chimera.updateDismissed", "0.33.0");
    mockGetVersion.mockResolvedValue(version({ latest: "0.33.0", update_available: true }));
    renderWithProviders(<VersionBadge />);

    expect(await screen.findByText("v0.32.2")).toBeInTheDocument();
    expect(screen.queryByText("v0.33.0 available")).not.toBeInTheDocument();
  });

  it("prompts again once a version NEWER than the dismissed one is released", async () => {
    localStorage.setItem("chimera.updateDismissed", "0.33.0");
    mockGetVersion.mockResolvedValue(version({ latest: "0.34.0", update_available: true }));
    renderWithProviders(<VersionBadge />);

    expect(await screen.findByRole("button", { name: "v0.34.0 available" })).toBeInTheDocument();
  });
});
