import { useEffect, useState } from "react";

import { useAppearance } from "@/lib/appearance";
import { anyInstalled, bundledFontLoads, fontInstalled } from "@/lib/fonts";
import { useT } from "@/lib/i18n";
import { useLayout } from "@/lib/layout/context";
import type { TranscriptWidth } from "@/lib/layout/model";
import type { CodeFont, Motion, TextSize, Theme, UiFont } from "@/lib/theme";

/**
 * The controls of Settings › Appearance, beside the language picker.
 *
 * Each is a plain select over a small closed set, named by the row it sits in (the caller passes the
 * row's label, the way `LanguageSelect` takes it from the row). The keys are written out in full rather
 * than assembled, so the i18n reachability test can see every one of them.
 */

const selectCls = "field h-8 w-56 px-2.5 text-sm";

interface Option<T extends string> {
  value: T;
  label: string;
  /**
   * Shown but not choosable, and why. The two reasons read differently on purpose: a code font is the
   * computer's own, so "not on this computer" is true and actionable; a font the app ships is not the
   * computer's business, and blaming the computer for a file the build lacks would send someone off
   * installing a font that would change nothing.
   */
  unavailable?: "missing" | "notInBuild";
}

function optionLabel(t: ReturnType<typeof useT>, o: Option<string>): string {
  if (o.unavailable === "missing") return t("settings.font.missing", { name: o.label });
  if (o.unavailable === "notInBuild") return t("settings.font.notInBuild", { name: o.label });
  return o.label;
}

function Choice<T extends string>({
  name,
  value,
  options,
  onChange,
}: {
  name: string;
  value: T;
  options: Option<T>[];
  onChange: (value: T) => void;
}) {
  const t = useT();
  return (
    <select
      className={selectCls}
      aria-label={name}
      value={value}
      onChange={(e) => onChange(e.target.value as T)}
    >
      {options.map((o) => (
        // The current choice stays choosable even when its font went missing: disabling the selected
        // option would leave the select showing a value it claims cannot be picked.
        <option key={o.value} value={o.value} disabled={o.unavailable !== undefined && o.value !== value}>
          {optionLabel(t, o)}
        </option>
      ))}
    </select>
  );
}

export function ThemeSelect({ name }: { name: string }) {
  const t = useT();
  const { theme, setTheme } = useAppearance();
  const options: Option<Theme>[] = [
    { value: "system", label: t("settings.theme.system") },
    { value: "light", label: t("settings.theme.light") },
    { value: "dark", label: t("settings.theme.dark") },
  ];
  return <Choice name={name} value={theme} options={options} onChange={setTheme} />;
}

export function MotionSelect({ name }: { name: string }) {
  const t = useT();
  const { motion, setMotion } = useAppearance();
  const options: Option<Motion>[] = [
    { value: "system", label: t("settings.motion.system") },
    { value: "full", label: t("settings.motion.full") },
    { value: "reduced", label: t("settings.motion.reduced") },
  ];
  return <Choice name={name} value={motion} options={options} onChange={setMotion} />;
}

export function TextSizeSelect({ name }: { name: string }) {
  const t = useT();
  const { textSize, setTextSize } = useAppearance();
  const options: Option<TextSize>[] = [
    { value: "small", label: t("settings.textSize.small") },
    { value: "medium", label: t("settings.textSize.medium") },
    { value: "large", label: t("settings.textSize.large") },
  ];
  return <Choice name={name} value={textSize} options={options} onChange={setTextSize} />;
}

/** Kept in the layout rather than beside the others: it is a property of the conversation screen, and
 *  the layout is what already travels to the server and back. */
export function TranscriptWidthSelect({ name }: { name: string }) {
  const t = useT();
  const { layout, dispatch } = useLayout();
  const options: Option<TranscriptWidth>[] = [
    { value: "narrow", label: t("settings.transcriptWidth.narrow") },
    { value: "medium", label: t("settings.transcriptWidth.medium") },
    { value: "wide", label: t("settings.transcriptWidth.wide") },
  ];
  return (
    <Choice
      name={name}
      value={layout.transcriptWidth}
      options={options}
      onChange={(width) => void dispatch({ type: "transcript-width", width })}
    />
  );
}

export function UiFontSelect({ name }: { name: string }) {
  const t = useT();
  const { uiFont, setUiFont } = useAppearance();
  // Declared by the app's own @font-face (public/fonts), so the question is whether the file loads,
  // not whether the computer has it. "pending" until the check answers: an option that appears and
  // then vanishes would be worse than one that appears a moment late.
  const [dyslexic, setDyslexic] = useState<boolean | null | "pending">("pending");
  useEffect(() => {
    let live = true;
    void bundledFontLoads("OpenDyslexic").then((ok) => {
      if (live) setDyslexic(ok);
    });
    return () => {
      live = false;
    };
  }, []);
  const options: Option<UiFont>[] = [
    { value: "system", label: t("settings.uiFont.system") },
    { value: "serif", label: t("settings.uiFont.serif") },
  ];
  // Offered only once the file is known to load (or the webview cannot tell, which fonts.ts treats as
  // available). A build without the file shows no OpenDyslexic at all, rather than an option that is
  // permanently disabled. The one exception is someone whose stored choice IS OpenDyslexic: the
  // select must be able to show its own value, so it stays, saying the build lacks it.
  const loads = dyslexic === true || dyslexic === null;
  if (loads || uiFont === "dyslexic") {
    options.push({
      value: "dyslexic",
      label: "OpenDyslexic",
      unavailable: dyslexic === false ? "notInBuild" : undefined,
    });
  }
  return <Choice name={name} value={uiFont} options={options} onChange={setUiFont} />;
}

export function CodeFontSelect({ name }: { name: string }) {
  const t = useT();
  const { codeFont, setCodeFont } = useAppearance();
  // Measured once per mount: fonts are not installed while a settings screen is open often enough to
  // be worth watching for.
  const [installed] = useState(() => ({
    // Either name will do: the stack in index.css lists both, and Windows ships them as a pair.
    cascadia: anyInstalled("Cascadia Code", "Cascadia Mono"),
    jetbrains: fontInstalled("JetBrains Mono"),
  }));
  const options: Option<CodeFont>[] = [
    { value: "default", label: t("settings.codeFont.default") },
    {
      value: "cascadia",
      label: "Cascadia Code",
      unavailable: installed.cascadia === false ? "missing" : undefined,
    },
    {
      value: "jetbrains",
      label: "JetBrains Mono",
      unavailable: installed.jetbrains === false ? "missing" : undefined,
    },
  ];
  return <Choice name={name} value={codeFont} options={options} onChange={setCodeFont} />;
}
