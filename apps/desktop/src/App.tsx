import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { IconRail, type View } from "@/components/IconRail";
import { useRoute } from "@/lib/router";
import { Settings } from "@/components/Settings";
import { Knowledge } from "@/components/Knowledge";
import { Automation } from "@/components/Automation";
import { Code } from "@/components/Code";
import { Work } from "@/components/Work";
import { Maturity } from "@/components/Maturity";
import { Onboarding } from "@/components/Onboarding";
import { Activity } from "@/components/Activity";
import { AppShell } from "@/components/shell/AppShell";
import { CommandPalette, type Command } from "@/components/shell/CommandPalette";
import { useHotkeys } from "@/lib/hotkeys";
import { AgentProvider } from "@/lib/agent-context";
import { RunSessionProvider } from "@/lib/run-session";
import { Spinner } from "@/components/ui/panel";
import { ErrorState } from "@/components/ui/async";
import { getDoctor } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { applyTheme, readTheme, resolveTheme, type Theme } from "@/lib/theme";
import { useIgnition } from "@/lib/useIgnition";

/**
 * The theme preference, persisted.
 *
 * The inline script in index.html has already painted the right theme by the time this runs; this
 * hook exists so the toggle can change it. `dark` is the *resolved* theme (what is on screen), so
 * the icon always matches reality even while the preference is "system".
 */
function useTheme() {
  const [theme, setTheme] = useState<Theme>(readTheme);
  const [dark, setDark] = useState(() => resolveTheme(readTheme()) === "dark");

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

  // The rail button is a two-state control over a three-state preference: tapping it commits to an
  // explicit choice, which is what someone reaching for the toggle means. "System" is set in
  // Settings › Appearance.
  return { dark, theme, setTheme, toggle: () => setTheme(dark ? "light" : "dark") };
}

export default function App() {
  const t = useT();
  const { dark, toggle } = useTheme();
  // The app's one piece of choreography. Runs on cold start only; see useIgnition.
  const ignite = useIgnition();
  // First-run gate: no provider key => show the Onboarding wizard instead of the app (a keyed user
  // never sees it). Session-local "skip" lets a GUI-first user jump to Settings without a key yet.
  const doctor = useQuery({ queryKey: ["doctor"], queryFn: getDoctor });
  const [skipOnboarding, setSkipOnboarding] = useState(false);
  // The screen lives in the URL now. It was a `useState`, which is enough until something outside
  // this switch needs to point at a place — "open this file at line 42" from a diagnostic or a
  // search hit — and until open editor tabs need to survive a reload and answer the back button.
  const { view, navigate } = useRoute();
  const [paletteOpen, setPaletteOpen] = useState(false);

  // Every destination, plus every session by title. This is the long tail the rail no longer
  // carries — and the reason collapsing the rail cost nothing in reach.
  const commands: Command[] = useMemo(() => {
    const views: View[] = ["code", "work", "knowledge", "automation", "settings"];
    return views.map((v) => ({
      id: `go-${v}`,
      label: t(`nav.${v}`),
      group: t("palette.group.go"),
      run: () => navigate(v),
    }));
  }, [t, navigate]);

  useHotkeys({
    onPalette: () => setPaletteOpen((o) => !o),
    onSettings: () => navigate("settings"),
    // The conversation owns its own "new" now — it lives beside the list of past ones, which is
    // where someone looks for it. A global shortcut that jumps you to a screen AND clears it is two
    // actions wearing one key.
    onNewChat: () => navigate("code"),
    onNavigate: (i) => {
      const order: View[] = ["code", "work", "knowledge", "automation"];
      const target = order[i - 1];
      if (target) navigate(target);
    },
  });

  if (doctor.isLoading) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-2 text-muted-foreground">
        <Spinner />
        <span className="text-sm">{t("app.starting")}</span>
      </div>
    );
  }
  if (doctor.isError) {
    return (
      <div className="flex h-full items-center justify-center">
        <ErrorState error={doctor.error} onRetry={() => doctor.refetch()} />
      </div>
    );
  }
  if (doctor.data && !doctor.data.has_any_key && !skipOnboarding) {
    return (
      <Onboarding
        onSkip={() => {
          setSkipOnboarding(true);
          navigate("settings");
        }}
      />
    );
  }

  return (
    // Stateful now: whichever screen is working publishes into it, so the footer keeps showing the
    // agent from anywhere. It used to be fed by the chat screen alone, which meant the merged
    // conversation would have left it mute.
    <AgentProvider>
      {/* Above the view switch on purpose: a run outlives the screen that started it, so its state
          cannot live inside that screen. This is what keeps the progress and the Stop alive when
          you navigate away mid-run. */}
      <RunSessionProvider>
      <CommandPalette open={paletteOpen} onOpenChange={setPaletteOpen} commands={commands} />
      <AppShell
        ignite={ignite}
        viewKey={view}
        viewLabel={t(`nav.${view}`)}
        // Usage lives in Settings now; the cost chip still takes you straight there.
        onOpenUsage={() => navigate("settings")}
        rail={
          <IconRail
            view={view}
            onSelect={navigate}
            dark={dark}
            onToggleTheme={toggle}
            ignite={ignite}
          />
        }
        // Right = what the agent is doing. The conversation keeps its own list of past sessions
        // inside itself (grouped by project, which the flat chat list could not do), so the left
        // slot stays empty rather than holding a second list of the same thing.
        inspector={view === "code" ? <Activity /> : undefined}
      >
        {view === "knowledge" && <Knowledge />}
        {view === "automation" && <Automation />}
        {view === "work" && <Work />}
        {view === "code" && <Code />}
        {/* Dev-only, matching the rail: in a shipped build this screen has no data. */}
        {view === "maturity" && import.meta.env.DEV && <Maturity />}
        {view === "settings" && <Settings />}
      </AppShell>
      </RunSessionProvider>
    </AgentProvider>
  );
}
