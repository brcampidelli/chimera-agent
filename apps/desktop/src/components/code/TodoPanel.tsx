import { useT } from "@/lib/i18n";

/** One line of the agent's own task list. */
export interface TodoEntry {
  task: string;
  status: string;
}

/**
 * The run's task list, as the agent declared it.
 *
 * The caption is the component. Every other structured thing this screen renders reports something
 * that was checked: `DiffView` shows a diff read off disk before and after the write, and the
 * verification badge shows a command's exit code. A row of ticks looks like more of the same, and
 * it is not — it is the model's own account of its progress, which nothing verified. So the panel
 * says whose claim it is, once, in words, above the list. Without that line this is a progress bar
 * that reports its own success.
 *
 * No colour carries meaning here on its own: the status word is written out beside every item, so
 * the list reads the same to someone who cannot distinguish the tints.
 */
export function TodoPanel({ items }: { items: TodoEntry[] }) {
  const t = useT();
  if (!items.length) return null;
  const done = items.filter((i) => i.status === "done").length;
  // Written out rather than composed as `code.todo.status.${status}`. The dead-key gate reads the
  // source for literal key strings, so an interpolated key is a key it cannot see — and the three
  // it could not see would be reported as unused and deleted by the next person tidying up.
  const label = (status: string) =>
    status === "done"
      ? t("code.todo.status.done")
      : status === "doing"
        ? t("code.todo.status.doing")
        : t("code.todo.status.pending");
  return (
    <section
      className="space-y-1 rounded-md border border-border p-2"
      aria-label={t("code.todo.title")}
    >
      <p className="text-xs text-muted-foreground">
        {t("code.todo.claimed", { done: String(done), total: String(items.length) })}
      </p>
      <ul className="space-y-0.5">
        {items.map((item, i) => (
          <li key={i} className="flex gap-2 text-xs">
            <span
              className={
                item.status === "done"
                  ? "text-ok-foreground"
                  : item.status === "doing"
                    ? "text-accent-ink"
                    : "text-muted-foreground"
              }
            >
              {label(item.status)}
            </span>
            <span className={item.status === "done" ? "text-muted-foreground" : undefined}>
              {item.task}
            </span>
          </li>
        ))}
      </ul>
    </section>
  );
}
