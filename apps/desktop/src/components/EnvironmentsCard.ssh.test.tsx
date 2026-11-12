import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { EnvironmentsCard } from "@/components/EnvironmentsCard";
import {
  allowSshHost,
  forgetSshHost,
  listCodeEnvironments,
  listSshHosts,
  listWslDistros,
  saveCodeEnvironment,
  type CodeEnvironments,
  type SshHosts,
} from "@/lib/api";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  listCodeEnvironments: vi.fn(),
  listWslDistros: vi.fn(),
  listSshHosts: vi.fn(),
  allowSshHost: vi.fn(),
  forgetSshHost: vi.fn(),
  saveCodeEnvironment: vi.fn(),
  removeCodeEnvironment: vi.fn(),
}));

const LOCAL = { id: "local", name: "Local", kind: "local", params: {}, builtin: true, available: true };
const PROD = {
  id: "s1", name: "Prod", kind: "ssh", params: { host: "web1", remote_path: "/srv/app" }, builtin: false, available: true,
};

const BOTH: CodeEnvironments = {
  environments: [LOCAL],
  kinds: [{ kind: "wsl", available: true }, { kind: "ssh", available: true }],
  defaults: {},
};

function hosts(allowed: string[]): SshHosts {
  return { supported: true, config_hosts: ["web1", "box"], allowed };
}

beforeEach(() => {
  vi.mocked(listCodeEnvironments).mockReset().mockResolvedValue(BOTH);
  vi.mocked(listWslDistros).mockReset().mockResolvedValue({ supported: true, distros: [] });
  vi.mocked(listSshHosts).mockReset().mockResolvedValue(hosts(["web1"]));
  vi.mocked(allowSshHost).mockReset();
  vi.mocked(forgetSshHost).mockReset();
  vi.mocked(saveCodeEnvironment).mockReset();
});

describe("EnvironmentsCard, SSH", () => {
  it("says SSH is not isolation and that nothing secret is typed", async () => {
    renderWithProviders(<EnvironmentsCard />);
    expect(await screen.findByText(/SSH is not isolation/)).toBeInTheDocument();
    expect(screen.getByText(/no user, password or key is typed here/)).toBeInTheDocument();
  });

  it("saves an SSH environment with an allowed alias and the remote folder, and nothing else", async () => {
    vi.mocked(saveCodeEnvironment).mockResolvedValue({ ...BOTH, environments: [LOCAL, PROD] });
    renderWithProviders(<EnvironmentsCard />);
    await userEvent.selectOptions(await screen.findByLabelText("Type"), "ssh");
    await userEvent.type(screen.getByLabelText("Name"), "Prod");
    await waitFor(() => expect(screen.getByRole("option", { name: "web1" })).toBeInTheDocument());
    expect(screen.getByLabelText("Host")).not.toHaveTextContent("box"); // not allowed: not offered
    await userEvent.selectOptions(screen.getByLabelText("Host"), "web1");
    expect(screen.getByRole("button", { name: "Add environment" })).toBeDisabled(); // folder missing
    await userEvent.type(screen.getByLabelText("Remote folder"), "/srv/app");
    await userEvent.click(screen.getByRole("button", { name: "Add environment" }));
    await waitFor(() =>
      expect(saveCodeEnvironment).toHaveBeenCalledWith({
        name: "Prod", kind: "ssh", params: { host: "web1", remote_path: "/srv/app" },
      }),
    );
  });

  it("shows a saved SSH environment as host and folder", async () => {
    vi.mocked(listCodeEnvironments).mockResolvedValue({ ...BOTH, environments: [LOCAL, PROD] });
    renderWithProviders(<EnvironmentsCard />);
    expect(await screen.findByText("SSH · web1:/srv/app")).toBeInTheDocument();
  });

  it("allows a host of the config that is not allowed yet", async () => {
    vi.mocked(allowSshHost).mockResolvedValue(hosts(["web1", "box"]));
    renderWithProviders(<EnvironmentsCard />);
    const section = await screen.findByTestId("ssh-hosts");
    await waitFor(() => expect(within(section).getByRole("option", { name: "box" })).toBeInTheDocument());
    expect(within(section).queryByRole("option", { name: "web1" })).toBeNull(); // already allowed
    await userEvent.selectOptions(within(section).getByLabelText("Host to allow"), "box");
    await userEvent.click(within(section).getByRole("button", { name: "Allow host" }));
    await waitFor(() => expect(allowSshHost).toHaveBeenCalledWith("box"));
  });

  it("stops allowing a host", async () => {
    vi.mocked(forgetSshHost).mockResolvedValue(hosts([]));
    renderWithProviders(<EnvironmentsCard />);
    await userEvent.click(await screen.findByRole("button", { name: "Stop allowing: web1" }));
    await waitFor(() => expect(forgetSshHost).toHaveBeenCalledWith("web1"));
  });

  it("says so when the server refuses the host", async () => {
    vi.mocked(allowSshHost).mockRejectedValue(new Error("no"));
    renderWithProviders(<EnvironmentsCard />);
    const section = await screen.findByTestId("ssh-hosts");
    await waitFor(() => expect(within(section).getByRole("option", { name: "box" })).toBeInTheDocument());
    await userEvent.selectOptions(within(section).getByLabelText("Host to allow"), "box");
    await userEvent.click(within(section).getByRole("button", { name: "Allow host" }));
    expect(await within(section).findByRole("alert")).toHaveTextContent("Could not change the list of hosts.");
  });

  it("offers no SSH where there is no ssh program", async () => {
    vi.mocked(listCodeEnvironments).mockResolvedValue({ ...BOTH, kinds: [{ kind: "wsl", available: true }] });
    renderWithProviders(<EnvironmentsCard />);
    expect(await screen.findByText(/SSH is not available here/)).toBeInTheDocument();
    expect(screen.queryByTestId("ssh-hosts")).toBeNull();
    expect(listSshHosts).not.toHaveBeenCalled();
  });
});
