import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  AccentPicker,
  CodeFontSelect,
  ContrastSelect,
  MotionSelect,
  TintSelect,
  PREVIEW_PATCH,
  TextSizeSelect,
  ThemeSelect,
  TranscriptViewSelect,
  TranscriptWidthSelect,
  UiZoomSelect,
  UiFontSelect,
} from "@/components/AppearanceControls";
import { IconRail } from "@/components/IconRail";
import { useAppearance } from "@/lib/appearance";
import { STORAGE_KEY } from "@/lib/layout/store";
import { ACCENT_KEY, THEME_KEY, UI_FONT_KEY } from "@/lib/theme";
import { TRANSCRIPT_VIEW_KEY } from "@/lib/transcriptView";
import { UI_ZOOM_KEY } from "@/lib/uiZoom";
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
  document.documentElement.style.zoom = "";
  delete document.documentElement.dataset.theme;
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
  it("swaps the interface font by attribute and offers no OpenDyslexic when this build lacks its file", async () => {
    let answer: (() => void) | undefined;
    Object.defineProperty(document, "fonts", {
      configurable: true,
      value: {
        load: () =>
          new Promise((_, reject) => {
            answer = () => reject(new Error("NetworkError"));
          }),
      },
    });
    const user = userEvent.setup();
    renderWithProviders(<UiFontSelect name="Interface font" />);
    // Wait for the check to have been asked, then let it fail, so the assertion below reads the row
    // AFTER the answer and not the moment before it.
    await waitFor(() => expect(answer).toBeDefined());
    answer?.();
    await waitFor(() => expect(screen.getAllByRole("option")).toHaveLength(2));

    // Not a disabled option blaming the computer: the font ships with the app or not at all.
    expect(screen.queryByRole("option", { name: /OpenDyslexic/ })).toBeNull();

    await user.selectOptions(screen.getByRole("combobox", { name: "Interface font" }), "serif");
    expect(document.documentElement.dataset.font).toBe("serif");
  });

  it("keeps a stored OpenDyslexic choice visible and says the build lacks the file, not the computer", async () => {
    localStorage.setItem(UI_FONT_KEY, "dyslexic");
    Object.defineProperty(document, "fonts", {
      configurable: true,
      value: { load: () => Promise.reject(new Error("NetworkError")) },
    });
    renderWithProviders(<UiFontSelect name="Interface font" />);

    const option = await screen.findByRole("option", { name: "OpenDyslexic (not available in this build)" });
    expect(option).toBeEnabled();
    expect(screen.getByRole("combobox", { name: "Interface font" })).toHaveValue("dyslexic");
    expect(screen.queryByRole("option", { name: /not on this computer/ })).toBeNull();
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

  it("previews a diff in both themes with the conversation's own DiffView, each half in its theme's scope", () => {
    // Inside a dark app: the light half must carry the light palette itself, not inherit the root's.
    document.documentElement.dataset.theme = "dark";
    renderWithProviders(<CodeFontSelect name="Code font" />);
    const group = screen.getByRole("group", { name: "Code diff preview" });
    const halves = group.querySelectorAll("figure");
    expect(halves).toHaveLength(2);
    expect([...halves].map((h) => h.getAttribute("data-theme-scope"))).toEqual(["light", "dark"]);
    for (const half of halves) {
      // `.theme-scope` is what re-declares the palette (index.css); `data-theme` alone on an inner
      // element is what the first version used, and it paints in the root's theme.
      expect(half).toHaveClass("theme-scope");
      expect(half).not.toHaveAttribute("data-theme");
      // DiffView's own markup, not a lookalike: the pre on surface-2, one line per kind.
      const pre = half.querySelector("pre");
      expect(pre).toHaveClass("bg-surface-2", "font-mono");
      expect(within(half).getByText("-const ready = false;")).toHaveClass("text-bad");
      expect(within(half).getByText("+const ready = true;")).toHaveClass("text-ok");
      expect(within(half).getByText("@@ example @@")).toHaveClass("text-accent/70");
    }
    expect(PREVIEW_PATCH.split("\n")).toHaveLength(3);
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
  it("persists the transcript view preference used as the next conversation's starting disclosure", async () => {
    const user = userEvent.setup();
    const { unmount } = renderWithProviders(<TranscriptViewSelect name="Transcript view" />);
    const row = screen.getByRole("combobox", { name: "Transcript view" });
    expect(row).toHaveValue("normal");
    // The middle view opens the agent's steps; the stream has no model reasoning to show, so the
    // label does not promise any.
    expect(within(row).getByRole("option", { name: "Steps" })).toHaveValue("reasoning");
    await user.selectOptions(row, "detailed");
    expect(localStorage.getItem(TRANSCRIPT_VIEW_KEY)).toBe("detailed");
    unmount();

    renderWithProviders(<TranscriptViewSelect name="Transcript view" />);
    expect(screen.getByRole("combobox", { name: "Transcript view" })).toHaveValue("detailed");
  });

  it("persists zoom and applies it to the document root", async () => {
    localStorage.removeItem(UI_ZOOM_KEY);
    document.documentElement.style.zoom = "";
    const user = userEvent.setup();
    const { unmount } = renderWithProviders(<UiZoomSelect name="Interface zoom" />);
    const row = screen.getByRole("combobox", { name: "Interface zoom" });
    await user.selectOptions(row, "110");
    expect(localStorage.getItem(UI_ZOOM_KEY)).toBe("110");
    expect(document.documentElement.style.zoom).toBe("110%");
    // Zoom is not Text size: it neither stamps nor clears the type scale.
    expect(document.documentElement.dataset.textSize).toBeUndefined();
    unmount();

    renderWithProviders(<UiZoomSelect name="Interface zoom" />);
    expect(screen.getByRole("combobox", { name: "Interface zoom" })).toHaveValue("110");
  });

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

describe("your own theme", () => {
  afterEach(() => {
    localStorage.clear();
    const d = document.documentElement.dataset;
    delete d.accent;
    delete d.tint;
    delete d.contrast;
  });

  it("picks an accent by clicking its swatch, and names every swatch", async () => {
    const user = userEvent.setup();
    renderWithProviders(<AccentPicker name="Accent colour" />);

    const group = screen.getByRole("radiogroup", { name: "Accent colour" });
    expect(screen.getAllByRole("radio")).toHaveLength(6);
    expect(screen.getByRole("radio", { name: "Blue" })).toHaveAttribute("aria-checked", "true");
    // The default stamps nothing, so the shipped theme is untouched until someone chooses.
    expect(document.documentElement.dataset.accent).toBeUndefined();

    await user.click(screen.getByRole("radio", { name: "Green" }));
    expect(document.documentElement.dataset.accent).toBe("green");
    expect(localStorage.getItem(ACCENT_KEY)).toBe("green");
    expect(group).toBeInTheDocument();
  });

  it("moves the choice with the arrow keys, like any radio group", async () => {
    const user = userEvent.setup();
    renderWithProviders(<AccentPicker name="Accent colour" />);

    // One tab stop: the chosen swatch.
    await user.tab();
    expect(screen.getByRole("radio", { name: "Blue" })).toHaveFocus();
    await user.keyboard("{ArrowRight}");
    expect(screen.getByRole("radio", { name: "Cyan" })).toHaveFocus();
    expect(document.documentElement.dataset.accent).toBe("cyan");
    // And it wraps, so the last colour is one key away from the first.
    await user.keyboard("{ArrowLeft}{ArrowLeft}");
    expect(document.documentElement.dataset.accent).toBe("graphite");
  });

  it("sets the surface tint and the contrast", async () => {
    const user = userEvent.setup();
    renderWithProviders(
      <>
        <TintSelect name="Background" />
        <ContrastSelect name="Contrast" />
      </>,
    );
    await user.selectOptions(screen.getByRole("combobox", { name: "Background" }), "warm");
    await user.selectOptions(screen.getByRole("combobox", { name: "Contrast" }), "high");
    expect(document.documentElement.dataset.tint).toBe("warm");
    expect(document.documentElement.dataset.contrast).toBe("high");
  });
});
