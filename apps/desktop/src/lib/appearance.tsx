import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from "react";

import {
  applyCodeFont,
  applyMotion,
  applyTextSize,
  applyTheme,
  applyUiFont,
  readCodeFont,
  readMotion,
  readTextSize,
  readTheme,
  readUiFont,
  resolveTheme,
  type CodeFont,
  type Motion,
  type TextSize,
  type Theme,
  type UiFont,
} from "@/lib/theme";

interface AppearanceApi {
  /** The preference, three-state. */
  theme: Theme;
  /** What is on screen now, so an icon matches reality even while the preference is "system". */
  dark: boolean;
  setTheme: (theme: Theme) => void;
  /** The rail button: a two-state control over a three-state preference, so it commits to an
   *  explicit choice — which is what someone reaching for it means. "System" is a row in Settings. */
  toggleTheme: () => void;
  motion: Motion;
  setMotion: (motion: Motion) => void;
  textSize: TextSize;
  setTextSize: (size: TextSize) => void;
  uiFont: UiFont;
  setUiFont: (font: UiFont) => void;
  codeFont: CodeFont;
  setCodeFont: (font: CodeFont) => void;
}

const AppearanceContext = createContext<AppearanceApi | null>(null);

/**
 * The appearance preferences, held once for the whole app.
 *
 * The theme used to be a hook local to `App`, which was enough while the rail button was its only
 * control. A Settings row with its own copy of the state would disagree with the button the moment
 * either was used — the button's icon would show the theme from before the row changed it — so both
 * read this one value instead.
 *
 * The inline script in index.html has already painted every one of these before this runs; the
 * effects below are how a change made at runtime reaches the page and storage.
 */
export function AppearanceProvider({ children }: { children: ReactNode }) {
  const [theme, setTheme] = useState<Theme>(readTheme);
  const [dark, setDark] = useState(() => resolveTheme(readTheme()) === "dark");
  const [motion, setMotion] = useState<Motion>(readMotion);
  const [textSize, setTextSize] = useState<TextSize>(readTextSize);
  const [uiFont, setUiFont] = useState<UiFont>(readUiFont);
  const [codeFont, setCodeFont] = useState<CodeFont>(readCodeFont);

  useEffect(() => {
    setDark(applyTheme(theme) === "dark");
  }, [theme]);

  useEffect(() => {
    // Follow the OS while the preference is "system" — someone flipping their system to night mode
    // mid-session should see the app follow, not wait for a restart.
    if (theme !== "system" || typeof matchMedia !== "function") return;
    const mq = matchMedia("(prefers-color-scheme: light)");
    const onChange = () => setDark(applyTheme("system") === "dark");
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, [theme]);

  useEffect(() => applyMotion(motion), [motion]);
  useEffect(() => applyTextSize(textSize), [textSize]);
  useEffect(() => applyUiFont(uiFont), [uiFont]);
  useEffect(() => applyCodeFont(codeFont), [codeFont]);

  const value = useMemo<AppearanceApi>(
    () => ({
      theme,
      dark,
      setTheme,
      toggleTheme: () => setTheme(dark ? "light" : "dark"),
      motion,
      setMotion,
      textSize,
      setTextSize,
      uiFont,
      setUiFont,
      codeFont,
      setCodeFont,
    }),
    [theme, dark, motion, textSize, uiFont, codeFont],
  );
  return <AppearanceContext.Provider value={value}>{children}</AppearanceContext.Provider>;
}

/** Loud outside the provider, like `useLayout`: a silent fallback would be a second copy of the
 *  state, which is the disagreement this context exists to remove. */
export function useAppearance(): AppearanceApi {
  const api = useContext(AppearanceContext);
  if (!api) throw new Error("useAppearance must be used inside <AppearanceProvider>");
  return api;
}
