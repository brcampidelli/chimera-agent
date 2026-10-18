import { useState } from "react";
import * as Menu from "@radix-ui/react-dropdown-menu";
import { Plus } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { useT } from "@/lib/i18n";
import { AddForm, BLANK } from "@/components/Mcp";
import { AddConnector } from "@/components/OpenApiConnectors";

type Which = "" | "mcp" | "openapi";

const ITEM =
  "flex cursor-default items-center gap-2 rounded-md px-2 py-1 text-sm outline-hidden data-highlighted:bg-surface-hover";

/**
 * "+ Add" on the Connectors screen (study 04, §2.1): a hand-written MCP server, or an HTTP API from
 * its OpenAPI description. Each opens the form the old screens had, unchanged — the MCP form writes
 * `mcp.json` only when Add is pressed, and the OpenAPI one adds a connector switched off with only
 * its GET operations ticked. A curated entry is set up from its own detail, not from here, because
 * that is where what limits it is said.
 */
export function AddConnectorMenu({
  onMcpAdded,
  onConnectorAdded,
}: {
  onMcpAdded: (name: string, test: boolean) => void;
  onConnectorAdded: () => void;
}) {
  const t = useT();
  const [which, setWhich] = useState<Which>("");
  const close = () => setWhich("");
  const titles: Record<Exclude<Which, "">, string> = {
    mcp: t("mcp.addServer"),
    openapi: t("connectors.addTitle"),
  };
  return (
    <>
      <Menu.Root>
        <Menu.Trigger asChild>
          <Button size="sm">
            <Plus className="h-4 w-4" />
            {t("connectorsUi.add")}
          </Button>
        </Menu.Trigger>
        <Menu.Portal>
          <Menu.Content align="end" sideOffset={4} className="overlay floating z-50 min-w-56 p-1 text-foreground">
            <Menu.Item className={ITEM} onSelect={() => setWhich("mcp")}>
              {t("connectorsUi.add.mcp")}
            </Menu.Item>
            <Menu.Item className={ITEM} onSelect={() => setWhich("openapi")}>
              {t("connectorsUi.add.openapi")}
            </Menu.Item>
          </Menu.Content>
        </Menu.Portal>
      </Menu.Root>
      <Dialog open={which !== ""} onOpenChange={(open) => !open && close()} title={which ? titles[which] : ""}>
        {which === "mcp" ? (
          <div className="-mx-4 space-y-2">
            <AddForm
              prefill={BLANK}
              onAdded={(name, test) => {
                close();
                onMcpAdded(name, test);
              }}
            />
            <p className="px-4 text-xs text-muted-foreground">{t("mcp.catalog.plaintext")}</p>
          </div>
        ) : null}
        {which === "openapi" ? (
          <div className="-mx-4 space-y-2">
            <p className="px-4 text-xs text-muted-foreground">{t("connectors.intro")}</p>
            <AddConnector
              onAdded={() => {
                close();
                onConnectorAdded();
              }}
            />
          </div>
        ) : null}
      </Dialog>
    </>
  );
}
