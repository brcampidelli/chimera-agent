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
  /** Shown but not choosable: a font this computer does not have. */
  missing?: boolean;
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
        <option key={o.value} value={o.value} disabled={o.missing === true && o.value !== value}>
          {o.missing ? t("settings.font.missing", { name: o.label }) : o.label}
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
  // Ships with the app (public/fonts), so the question is whether the file loads, not whether the
  // computer has it.
  const [dyslexic, setDyslexic] = useState<boolean | null>(null);
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
    { value: "dyslexic", label: "OpenDyslexic", missing: dyslexic === false },
  ];
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
    { value: "cascadia", label: "Cascadia Code", missing: installed.cascadia === false },
    { value: "jetbrains", label: "JetBrains Mono", missing: installed.jetbrains === false },
  ];
  return <Choice name={name} value={codeFont} options={options} onChange={setCodeFont} />;
}
