import { Dialog } from "@/components/ui/dialog";
import { DESIGN_SHORTCUTS, displayKeys } from "@/lib/design/shortcuts";
import { useT } from "@/lib/i18n";

/**
 * Every canvas shortcut and gesture, read from the same table the key handler matches on, so the
 * sheet cannot list a key that does nothing or miss one that does. Opened by `?` on the canvas and
 * by the keyboard button in the toolbar.
 */
export function ShortcutsHelp({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
  const t = useT();
  return (
    <Dialog open={open} onOpenChange={onOpenChange} title={t("design.keys.title")} description={t("design.keys.blurb")}>
      <table className="w-full text-sm">
        <thead className="sr-only">
          <tr>
            <th scope="col">{t("design.keys.action")}</th>
            <th scope="col">{t("design.keys.keys")}</th>
          </tr>
        </thead>
        <tbody>
          {DESIGN_SHORTCUTS.map((row) => (
            <tr key={row.id} className="border-b border-hairline last:border-0">
              <td className="py-1.5 pr-4 text-foreground">{t(row.labelKey)}</td>
              <td className="py-1.5 text-right">
                <kbd className="rounded-sm bg-surface-2 px-1.5 py-0.5 font-mono text-xs text-muted-foreground">{displayKeys(row)}</kbd>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </Dialog>
  );
}
