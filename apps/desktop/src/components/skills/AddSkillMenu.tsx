import { useState } from "react";
import * as Menu from "@radix-ui/react-dropdown-menu";
import { Plus } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { ITEM } from "@/components/catalog/filterLabels";
import { SkillUpload } from "@/components/SkillUpload";
import { useT } from "@/lib/i18n";

/**
 * "+ Add" (study 04, §2.1): the screen's main action, so primary. Uploading opens the existing
 * upload panel — its consent sentence, its read-before-on rule and its "replace it" answer to a
 * taken name come with it unchanged. The library is not a form: it is a place on this screen.
 */
export function AddSkillMenu({ onLibrary }: { onLibrary: () => void }) {
  const t = useT();
  const [upload, setUpload] = useState(false);
  return (
    <>
      <Menu.Root>
        <Menu.Trigger asChild>
          <Button size="sm" variant="primary">
            <Plus className="h-4 w-4" />
            {t("skills.add")}
          </Button>
        </Menu.Trigger>
        <Menu.Portal>
          <Menu.Content align="end" sideOffset={4} className="overlay floating z-50 min-w-56 p-1 text-foreground">
            <Menu.Item className={ITEM} onSelect={() => setUpload(true)}>
              {t("skills.add.upload")}
            </Menu.Item>
            <Menu.Item className={ITEM} onSelect={onLibrary}>
              {t("skills.discover.library")}
            </Menu.Item>
          </Menu.Content>
        </Menu.Portal>
      </Menu.Root>
      <Dialog open={upload} onOpenChange={setUpload} title={t("skills.add.upload")}>
        <SkillUpload />
      </Dialog>
    </>
  );
}
