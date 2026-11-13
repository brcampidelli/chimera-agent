export type AutomationTab = "schedule" | "tasks" | "agents";

const AUTOMATION_TABS: readonly AutomationTab[] = ["schedule", "tasks", "agents"];

/** A `?tab=` value from the URL, or the first tab for anything else — a stale link lands on the
 *  screen rather than on nothing. Same contract as `settingsTab`. */
export function automationTab(value: string | null | undefined): AutomationTab {
  return AUTOMATION_TABS.find((tab) => tab === value) ?? "schedule";
}
