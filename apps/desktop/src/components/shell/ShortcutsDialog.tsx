import { Dialog } from "@/components/ui/dialog";
import { formatKeys, SHORTCUTS } from "@/lib/hotkeys";
import { useT } from "@/lib/i18n";

/**
 * Help › Keyboard shortcuts (⌘;): every row of `SHORTCUTS`, in its order, and nothing else.
 *
 * Rendered from the table the listeners match on, so a key added, moved or removed there is the
 * sheet's next state without anyone remembering to edit it — the sheet cannot advertise a chord the
 * app does not answer.
 */
export function ShortcutsDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
  const t = useT();
  return (
    <Dialog open={open} onOpenChange={onOpenChange} title={t("hotkeys.title")}>
      <dl className="max-h-96 overflow-y-auto">
        {SHORTCUTS.map((shortcut) => (
          <div key={shortcut.id} className="flex items-center justify-between gap-4 border-b border-hairline py-2 text-sm">
            <dt>{t(shortcut.labelKey)}</dt>
            <dd>
              <kbd className="rounded border border-hairline px-1.5 py-0.5 font-mono text-xs">{formatKeys(shortcut.keys)}</kbd>
            </dd>
          </div>
        ))}
      </dl>
    </Dialog>
  );
}
