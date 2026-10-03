import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  CodeFontSelect,
  MotionSelect,
  TextSizeSelect,
  ThemeSelect,
  TranscriptWidthSelect,
  UiFontSelect,
} from "@/components/AppearanceControls";
import { IconRail } from "@/components/IconRail";
import { useAppearance } from "@/lib/appearance";
import { STORAGE_KEY } from "@/lib/layout/store";
import { THEME_KEY } from "@/lib/theme";
import { renderWithProviders } from "@/test/utils";

/**
 * Settings › Appearance (study 29, P1.1, P1.2 and P1.6). The rows existed in comments — App.tsx and
 * DESIGN.md both pointed at "System" in Settings › Appearance — and nowhere on screen: one click on the
 * rail's theme button and there was no way back to following the computer.
 */

/** The OS says light; everything else does not match. */
function osPrefersLight(): void {
  vi.stubGlobal(
    "matchMedia",
    vi.fn((query: string) => ({
      matches: query === "(prefers-color-scheme: light)",
      media: query,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    })),
  );
}

/** The rail exactly as App wires it, so the test sees what the person sees beside the row. */
function Rail() {
  const { dark, toggleTheme } = useAppearance();
  return <IconRail view="code" onSelect={() => {}} dark={dark} onToggleTheme={toggleTheme} ignite={false} />;
}

afterEach(() => {
  vi.unstubAllGlobals();
  Reflect.deleteProperty(document, "fonts");
});

describe("the theme row", () => {
  it("goes back to following the computer when System is chosen after Dark", async () => {
    osPrefersLight();
    const user = userEvent.setup();
    renderWithProviders(<ThemeSelect name="Theme" />);

    await user.selectOptions(screen.getByRole("combobox", { name: "Theme" }), "dark");
    expect(document.documentElement.dataset.theme).toBe("dark");

    await user.selectOptions(screen.getByRole("combobox", { name: "Theme" }), "system");
    // The computer says light, so the page is light again — and "system" is what is kept.
    expect(document.documentElement.dataset.theme).toBe("light");
    expect(localStorage.getItem(THEME_KEY)).toBe("system");
  });

  it("agrees with the rail's theme button in both directions", async () => {
    const user = userEvent.setup();
    renderWithProviders(
      <>
        <Rail />
        <ThemeSelect name="Theme" />
      </>,
    );
    const row = screen.getByRole("combobox", { name: "Theme" });
    expect(row).toHaveValue("system");

    // The button commits to an explicit theme; the row shows the one it chose.
    await user.click(screen.getByRole("button", { name: "Light theme" }));
    expect(row).toHaveValue("light");
    expect(document.documentElement.dataset.theme).toBe("light");

    // And the row changes what the button offers.
    await user.selectOptions(row, "dark");
    expect(screen.getByRole("button", { name: "Light theme" })).toBeInTheDocument();
  });
});

describe("the motion row", () => {
  it("overrides the computer with Reduced and hands control back with System", async () => {
    const user = userEvent.setup();
    renderWithProviders(<MotionSelect name="Motion" />);
    const row = screen.getByRole("combobox", { name: "Motion" });

    await user.selectOptions(row, "reduced");
    expect(document.documentElement.dataset.motion).toBe("reduced");
    await user.selectOptions(row, "system");
    expect(document.documentElement.dataset.motion).toBeUndefined();
  });
});

describe("the text size row", () => {
  it("stamps Large on the page and removes the stamp for Medium", async () => {
    const user = userEvent.setup();
    renderWithProviders(<TextSizeSelect name="Text size" />);
    const row = screen.getByRole("combobox", { name: "Text size" });

    await user.selectOptions(row, "large");
    expect(document.documentElement.dataset.textSize).toBe("large");
    await user.selectOptions(row, "medium");
    expect(document.documentElement.dataset.textSize).toBeUndefined();
  });
});

describe("the conversation width row", () => {
  it("keeps the width in the layout, which is what reaches the server", async () => {
    const user = userEvent.setup();
    renderWithProviders(<TranscriptWidthSelect name="Conversation width" />);
    const row = screen.getByRole("combobox", { name: "Conversation width" });
    expect(row).toHaveValue("medium");

    await user.selectOptions(row, "wide");
    await waitFor(() =>
      expect(JSON.parse(localStorage.getItem(STORAGE_KEY) ?? "{}").transcriptWidth).toBe("wide"),
    );
  });
});

describe("the font rows", () => {
  it("swaps the interface font by attribute and marks OpenDyslexic when its file does not load", async () => {
    Object.defineProperty(document, "fonts", {
      configurable: true,
      value: { load: () => Promise.reject(new Error("NetworkError")) },
    });
    const user = userEvent.setup();
    renderWithProviders(<UiFontSelect name="Interface font" />);

    const dyslexic = await screen.findByRole("option", { name: "OpenDyslexic (not on this computer)" });
    expect(dyslexic).toBeDisabled();

    await user.selectOptions(screen.getByRole("combobox", { name: "Interface font" }), "serif");
    expect(document.documentElement.dataset.font).toBe("serif");
  });

  it("offers OpenDyslexic when its file loads", async () => {
    Object.defineProperty(document, "fonts", { configurable: true, value: { load: () => Promise.resolve([{}]) } });
    const user = userEvent.setup();
    renderWithProviders(<UiFontSelect name="Interface font" />);
    // Wait for the check to come back before choosing, so the option is in its final state.
    await waitFor(() => expect(screen.getByRole("option", { name: "OpenDyslexic" })).toBeEnabled());

    await user.selectOptions(screen.getByRole("combobox", { name: "Interface font" }), "dyslexic");
    expect(document.documentElement.dataset.font).toBe("dyslexic");
  });

  it("swaps the code font by attribute and stamps nothing for Default", async () => {
    const user = userEvent.setup();
    renderWithProviders(<CodeFontSelect name="Code font" />);
    const row = screen.getByRole("combobox", { name: "Code font" });

    await user.selectOptions(row, "jetbrains");
    expect(document.documentElement.dataset.fontCode).toBe("jetbrains");
    await user.selectOptions(row, "default");
    expect(document.documentElement.dataset.fontCode).toBeUndefined();
  });
});

describe("the defaults", () => {
  it("are the page as it was before the rows existed: nothing stamped, every row on its default", () => {
    renderWithProviders(
      <>
        <ThemeSelect name="Theme" />
        <MotionSelect name="Motion" />
        <TextSizeSelect name="Text size" />
        <TranscriptWidthSelect name="Conversation width" />
        <UiFontSelect name="Interface font" />
        <CodeFontSelect name="Code font" />
      </>,
    );
    expect(screen.getByRole("combobox", { name: "Theme" })).toHaveValue("system");
    expect(screen.getByRole("combobox", { name: "Motion" })).toHaveValue("system");
    expect(screen.getByRole("combobox", { name: "Text size" })).toHaveValue("medium");
    expect(screen.getByRole("combobox", { name: "Conversation width" })).toHaveValue("medium");
    expect(screen.getByRole("combobox", { name: "Interface font" })).toHaveValue("system");
    expect(screen.getByRole("combobox", { name: "Code font" })).toHaveValue("default");
    const root = document.documentElement.dataset;
    expect([root.motion, root.textSize, root.font, root.fontCode]).toEqual([undefined, undefined, undefined, undefined]);
  });
});
