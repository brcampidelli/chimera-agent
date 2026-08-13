import { FileTree } from "@/components/editor/FileTree";
import { useT } from "@/lib/i18n";

/**
 * The left slot of the editor screen: which folder, and what is in it.
 *
 * The workspace field is the same choice the chat screen makes and is stored in the same place
 * (`lib/workspace.ts`), so picking a folder on one screen is picking it on both. Two independent
 * "current project" values in one app is a bug that presents as the agent editing the wrong tree.
 */
export function EditSidebar({
  workspace,
  onWorkspace,
  activePath,
  onOpen,
}: {
  workspace: string;
  onWorkspace: (value: string) => void;
  activePath: string | null;
  onOpen: (path: string) => void;
}) {
  const t = useT();
  return (
    <div className="flex h-full flex-col">
      <div className="shrink-0 border-b border-hairline p-2">
        <label className="sr-only" htmlFor="edit-workspace">
          {t("code.workspace")}
        </label>
        <input
          id="edit-workspace"
          value={workspace}
          onChange={(e) => onWorkspace(e.target.value)}
          placeholder={t("code.workspacePlaceholder")}
          className="field w-full px-2 text-xs"
        />
      </div>
      <div className="min-h-0 flex-1">
        <FileTree workspace={workspace || null} activePath={activePath} onOpen={onOpen} />
      </div>
    </div>
  );
}
