import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { EnvironmentPicker } from "@/components/code/EnvironmentPicker";
import {
  listCodeEnvironments,
  listCodeSessions,
  setCodeEnvironmentDefault,
  type CodeEnvironments,
} from "@/lib/api";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  listCodeEnvironments: vi.fn(),
  listCodeSessions: vi.fn(),
  setCodeEnvironmentDefault: vi.fn(),
}));

const WS = "C:\\work\\shop";
const LOCAL = { id: "local", name: "Local", kind: "local", params: {}, builtin: true, available: true };
const UBUNTU = { id: "e1", name: "Ubuntu dev", kind: "wsl", params: { distro: "Ubuntu" }, builtin: false, available: true };

function listing(over: Partial<CodeEnvironments> = {}): CodeEnvironments {
  return { environments: [LOCAL, UBUNTU], kinds: [{ kind: "wsl", available: true }], defaults: {}, project_default: "", ...over };
}

function mount(props: Partial<Parameters<typeof EnvironmentPicker>[0]> = {}) {
  const onChange = vi.fn();
  renderWithProviders(
    <EnvironmentPicker workspace={WS} sessionId={null} started={false} value="" onChange={onChange} {...props} />,
  );
  return onChange;
}

beforeEach(() => {
  vi.mocked(listCodeEnvironments).mockReset();
  vi.mocked(listCodeSessions).mockReset();
  vi.mocked(setCodeEnvironmentDefault).mockReset();
});

describe("EnvironmentPicker", () => {
  it("draws nothing while there is nothing to choose", async () => {
    vi.mocked(listCodeEnvironments).mockResolvedValue(listing({ environments: [LOCAL] }));
    mount();
    await waitFor(() => expect(listCodeEnvironments).toHaveBeenCalled());
    expect(screen.queryByTestId("environment-picker")).toBeNull();
  });

  it("offers the saved environments before the first message and reports the pick", async () => {
    vi.mocked(listCodeEnvironments).mockResolvedValue(listing());
    const onChange = mount();
    const select = await screen.findByRole("combobox", { name: "Environment" });
    expect(select).toHaveValue("local");
    await userEvent.selectOptions(select, "e1");
    expect(onChange).toHaveBeenCalledWith("e1");
  });

  it("starts on the project's default and says so", async () => {
    vi.mocked(listCodeEnvironments).mockResolvedValue(listing({ project_default: "e1" }));
    mount();
    expect(await screen.findByRole("combobox", { name: "Environment" })).toHaveValue("e1");
    expect(screen.getByText("Default for this project")).toBeInTheDocument();
  });

  it("makes the pick the project's default on request", async () => {
    vi.mocked(listCodeEnvironments).mockResolvedValue(listing());
    vi.mocked(setCodeEnvironmentDefault).mockResolvedValue(listing());
    mount({ value: "e1" });
    await userEvent.click(await screen.findByRole("button", { name: "Use for new conversations in this project" }));
    await waitFor(() => expect(setCodeEnvironmentDefault).toHaveBeenCalledWith(WS, "e1"));
  });

  it("does not offer an environment this machine cannot run", async () => {
    vi.mocked(listCodeEnvironments).mockResolvedValue(
      listing({ environments: [LOCAL, { ...UBUNTU, available: false }] }),
    );
    mount();
    const option = await screen.findByRole("option", { name: /Ubuntu dev/ });
    expect(option).toBeDisabled();
  });

  it("locks to the conversation's own environment after the first message", async () => {
    vi.mocked(listCodeEnvironments).mockResolvedValue(listing());
    vi.mocked(listCodeSessions).mockResolvedValue([
      { id: "s1", environment: { id: "e1", name: "Ubuntu dev", kind: "wsl" } } as never,
    ]);
    const onChange = mount({ started: true, sessionId: "s1" });
    const fixed = await screen.findByTestId("environment-fixed");
    expect(fixed).toHaveTextContent("Ubuntu dev");
    expect(screen.queryByRole("combobox")).toBeNull();
    expect(onChange).not.toHaveBeenCalled();
  });

  it("says when the environment a conversation ran in was removed", async () => {
    vi.mocked(listCodeEnvironments).mockResolvedValue(listing({ environments: [LOCAL] }));
    vi.mocked(listCodeSessions).mockResolvedValue([
      { id: "s1", environment: { id: "gone", name: "Old distro", kind: "wsl" } } as never,
    ]);
    mount({ started: true, sessionId: "s1" });
    expect(await screen.findByTestId("environment-fixed")).toHaveTextContent("(removed)");
  });

  it("draws nothing for a started conversation that is local", async () => {
    vi.mocked(listCodeEnvironments).mockResolvedValue(listing());
    vi.mocked(listCodeSessions).mockResolvedValue([{ id: "s1" } as never]);
    mount({ started: true, sessionId: "s1" });
    await waitFor(() => expect(listCodeSessions).toHaveBeenCalled());
    expect(screen.queryByTestId("environment-fixed")).toBeNull();
    expect(screen.queryByRole("combobox")).toBeNull();
  });
});
