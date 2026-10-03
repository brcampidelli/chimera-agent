import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { KeepAwakeCard } from "@/components/KeepAwakeCard";
import { KeepAwakeIndicator } from "@/components/shell/KeepAwakeIndicator";
import type { KeepAwakeState } from "@/lib/types";
import { renderWithProviders as render } from "@/test/utils";

/**
 * Keeping the computer awake while there is work (study 29, P2.5): the switch, and the line that
 * says when it is acting.
 *
 * The status line is the honesty half. A machine that will not sleep and says nothing about why is
 * the version of this people uninstall, so the line names what holds it — and renders nothing at all
 * when nothing does, including when the switch is on but the keeper is blocked (battery, or a
 * system with no mechanism), because "keeping awake" there would be a claim about the OS that is
 * false.
 */
vi.mock("@/lib/api", () => ({ getKeepAwake: vi.fn() }));

const api = await import("@/lib/api");

function state(over: Partial<KeepAwakeState> = {}): KeepAwakeState {
  return {
    mode: "working",
    active: false,
    reasons: [],
    blocked: "",
    mechanism: "windows",
    on_battery_allowed: false,
    ...over,
  };
}

beforeEach(() => vi.clearAllMocks());

describe("the status bar line", () => {
  it("names what is keeping the machine awake while it is", async () => {
    vi.mocked(api.getKeepAwake).mockResolvedValue(state({ active: true, reasons: ["cron", "turn"] }));
    render(<KeepAwakeIndicator />);

    expect(
      await screen.findByText("Keeping awake: a scheduled task, a coding turn"),
    ).toBeInTheDocument();
  });

  it("says nothing while the machine is not being held", async () => {
    // Work present, machine not held (on battery). Rendered beside the card, which reads the same
    // query: its battery sentence appearing proves the answer arrived, so the absence below is the
    // indicator deciding, not the indicator still waiting.
    vi.mocked(api.getKeepAwake).mockResolvedValue(state({ active: false, reasons: ["turn"], blocked: "battery" }));
    render(
      <>
        <KeepAwakeIndicator />
        <KeepAwakeCard mode="working" onBattery={false} onSave={vi.fn()} />
      </>,
    );

    expect(await screen.findByRole("status")).toHaveTextContent(/on battery/i);
    expect(screen.queryByText(/keeping awake/i)).not.toBeInTheDocument();
  });
});

describe("the settings card", () => {
  it("ships off and saves the mode the person picks", async () => {
    vi.mocked(api.getKeepAwake).mockResolvedValue(state({ mode: "off" }));
    const onSave = vi.fn();
    render(<KeepAwakeCard mode="off" onBattery={false} onSave={onSave} />);

    expect(screen.getByRole("radio", { name: /^off/i })).toBeChecked();
    await userEvent.click(screen.getByRole("radio", { name: /while there is work/i }));

    expect(onSave).toHaveBeenCalledWith({ CHIMERA_KEEP_AWAKE: "working" });
  });

  it("says that a closed lid still sleeps", () => {
    vi.mocked(api.getKeepAwake).mockResolvedValue(state({ mode: "off" }));
    render(<KeepAwakeCard mode="off" onBattery={false} onSave={vi.fn()} />);

    expect(screen.getByText(/prevents idle sleep only/i)).toBeInTheDocument();
  });

  it("keeps the battery switch inert while the mode is off, and saves it once on", async () => {
    vi.mocked(api.getKeepAwake).mockResolvedValue(state());
    const onSave = vi.fn();
    const { rerender } = render(<KeepAwakeCard mode="off" onBattery={false} onSave={onSave} />);
    expect(screen.getByRole("switch", { name: "Also on battery" })).toBeDisabled();

    rerender(<KeepAwakeCard mode="working" onBattery={false} onSave={onSave} />);
    await userEvent.click(screen.getByRole("switch", { name: "Also on battery" }));

    expect(onSave).toHaveBeenCalledWith({ CHIMERA_KEEP_AWAKE_ON_BATTERY: "true" });
  });

  it("says when there is work and the machine is not held, and why", async () => {
    vi.mocked(api.getKeepAwake).mockResolvedValue(state({ reasons: ["turn"], blocked: "unsupported", mechanism: "none" }));
    render(<KeepAwakeCard mode="working" onBattery={false} onSave={vi.fn()} />);

    expect(await screen.findByRole("status")).toHaveTextContent(/no way to hold it awake/i);
  });
});
