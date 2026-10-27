import { useState, type ReactNode } from "react";
import { BadgeCheck, ExternalLink } from "lucide-react";

import { Badge } from "@/components/ui/panel";
import { focusRing } from "@/components/ui/focus";
import { useT } from "@/lib/i18n";
import { cn } from "@/lib/utils";
import type { McpCatalogEntry } from "@/lib/api";
import type { Connector, McpServer, PublicCatalog, PublicCatalogItem } from "@/lib/types";
import { AddForm, entryText, McpServerRow, toPrefill, type McpControls } from "@/components/Mcp";
import { ConnectorRow } from "@/components/OpenApiConnectors";
import { CapabilityLine } from "@/components/catalog/CapabilityLine";
import { CatalogDetail } from "@/components/catalog/CatalogDetail";
import { badgeOf } from "@/components/catalog/model";
import {
  curatedItemId,
  curatedProof,
  fromCuratedEntry,
  fromPublicItem,
  mcpItemId,
  openapiItemId,
  publicItemId,
  serverCommand,
} from "@/components/connectors/connectorItems";

export type ConnectorTarget =
  | { kind: "mcp"; server: McpServer }
  | { kind: "openapi"; connector: Connector }
  | { kind: "curated"; entry: McpCatalogEntry; configured: boolean }
  | { kind: "public"; item: PublicCatalogItem };

/** What an `item=` in the URL points at, or `null` when it names nothing that exists any more. */
export function resolveConnector(
  id: string | null,
  servers: readonly McpServer[],
  connectors: readonly Connector[],
  entries: readonly McpCatalogEntry[],
  publicCatalog: PublicCatalog | undefined,
): ConnectorTarget | null {
  if (!id) return null;
  const server = servers.find((s) => mcpItemId(s.name) === id);
  if (server) return { kind: "mcp", server };
  const connector = connectors.find((c) => openapiItemId(c.name) === id);
  if (connector) return { kind: "openapi", connector };
  const entry = entries.find((e) => curatedItemId(e.id) === id);
  if (entry) return { kind: "curated", entry, configured: servers.some((s) => s.name === entry.id) };
  const item = (publicCatalog?.items ?? []).find((i) => publicItemId(i.id) === id);
  if (item) return { kind: "public", item };
  return null;
}

const LINK = cn("inline-flex items-center gap-1 rounded-md text-xs text-accent-ink hover:underline", focusRing);

function Facts({ rows }: { rows: readonly [string, ReactNode][] }) {
  return (
    <dl className="grid grid-cols-3 gap-x-3 gap-y-2 text-xs">
      {rows.map(([label, value]) => (
        <div key={label} className="contents">
          <dt className="text-muted-foreground">{label}</dt>
          <dd className="col-span-2 min-w-0 break-words">{value}</dd>
        </div>
      ))}
    </dl>
  );
}

/**
 * A curated entry: what it gives, what limits it — said whole here, the card only starts the
 * sentence — and the form that sets it up. The secrets it asks for are typed in this form, into
 * password fields, and never displayed: the list above it names the keys, not the values.
 */
function CuratedBody({
  entry,
  configured,
  onAdded,
}: {
  entry: McpCatalogEntry;
  configured: boolean;
  onAdded: (name: string, test: boolean) => void;
}) {
  const t = useT();
  const { summary, containment } = entryText(entry, t);
  const item = fromCuratedEntry(entry, undefined, t);
  const vendor = badgeOf({ proof: curatedProof(entry) }) === "vendor";
  // Remounting the form after a save puts the blank pick back, as the old screen did.
  const [round, setRound] = useState(0);
  const rows: [string, ReactNode][] = [
    [
      t("connectorsUi.detail.publisher"),
      <span key="p" className="inline-flex flex-wrap items-center gap-1.5">
        {vendor ? (
          <span className="inline-flex items-center gap-1 text-accent-ink" data-badge="vendor">
            <BadgeCheck aria-hidden className="h-3.5 w-3.5" />
            {t("catalogUi.badge.vendor")}
          </span>
        ) : null}
        <Badge tone={entry.official ? "accent" : "muted"}>
          {entry.official ? t("mcp.catalog.official") : t("mcp.catalog.community")}
        </Badge>
      </span>,
    ],
  ];
  if (entry.repo)
    rows.push([
      t("connectorsUi.detail.source"),
      <a key="r" className={LINK} href={`https://github.com/${entry.repo}`} target="_blank" rel="noreferrer">
        <span className="font-mono">{entry.repo}</span>
        <ExternalLink aria-hidden className="h-3 w-3" />
      </a>,
    ]);
  if (entry.docs)
    rows.push([
      t("mcp.catalog.docs"),
      <a key="d" className={LINK} href={entry.docs} target="_blank" rel="noreferrer">
        {t("connectorsUi.detail.readDocs")}
        <ExternalLink aria-hidden className="h-3 w-3" />
      </a>,
    ]);
  return (
    <div className="flex flex-col gap-4">
      <p className="text-sm text-foreground">{summary}</p>
      <section className="flex flex-col gap-1.5 rounded-chip border border-hairline bg-surface-2 px-3 py-2">
        <CapabilityLine facts={item.capabilities} />
        <h4 className="text-xs font-semibold text-muted-foreground">{t("connectorsUi.detail.limit")}</h4>
        {/* The field this whole catalogue exists to be honest about. For most of these the limit is
            the CREDENTIAL, and a one-word "read-only" chip would say the opposite. */}
        <p className="text-xs text-foreground">{containment}</p>
        {entry.secrets.length ? (
          <p className="text-xs text-muted-foreground">
            {t("mcp.catalog.asks", { n: entry.secrets.map((s) => s.key).join(", ") })}
          </p>
        ) : null}
      </section>
      <Facts rows={rows} />
      <section className="flex flex-col gap-2">
        <h4 className="text-sm font-semibold">{t("connectorsUi.detail.setUp")}</h4>
        {configured ? <p className="text-xs text-warn-foreground">{t("connectorsUi.detail.configured")}</p> : null}
        {entry.available ? (
          <div className="-mx-4">
            <AddForm
              key={`${entry.id}-${round}`}
              prefill={toPrefill(entry)}
              onAdded={(name, test) => {
                setRound((n) => n + 1);
                onAdded(name, test);
              }}
            />
          </div>
        ) : entry.url ? (
          <p className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
            {t("mcp.catalog.startLocalService", { name: entry.label })}
            {entry.install_url && (
              <a href={entry.install_url} target="_blank" rel="noreferrer" className={LINK}>
                {t("mcp.catalog.installLocal", { name: entry.label })}
                <ExternalLink aria-hidden className="h-3 w-3" />
              </a>
            )}
          </p>
        ) : (
          // Shown rather than hidden: "install docker first" is actionable.
          <p className="text-xs text-muted-foreground">{t("mcp.catalog.needs", { n: entry.runner })}</p>
        )}
        {/* Said here, next to the fields that take one: a value typed into this form lands in
            mcp.json as text. */}
        <p className="text-xs text-muted-foreground">{t("mcp.catalog.plaintext")}</p>
      </section>
    </div>
  );
}

/** An entry of the public catalogue: what it is, who publishes it, and where it was read. */
function PublicBody({ item }: { item: PublicCatalogItem }) {
  const t = useT();
  const card = fromPublicItem(item, t);
  const src = item.source;
  const rows: [string, ReactNode][] = [];
  if (item.author) rows.push([t("connectorsUi.detail.publisher"), item.author]);
  rows.push([
    t("catalogUi.group.license"),
    item.license ? item.license : <span className="text-warn-foreground">{t("catalogUi.licenseUnread")}</span>,
  ]);
  rows.push([t("catalogUi.group.cat"), item.category]);
  rows.push([
    t("connectorsUi.detail.source"),
    <a
      key="s"
      className={LINK}
      href={`https://github.com/${src.owner}/${src.repo}/blob/${src.sha}/${src.path}`}
      target="_blank"
      rel="noreferrer"
    >
      <span className="break-all font-mono">{`${src.owner}/${src.repo}@${src.sha.slice(0, 7)} · ${src.path}`}</span>
      <ExternalLink aria-hidden className="h-3 w-3 shrink-0" />
    </a>,
  ]);
  return (
    <div className="flex flex-col gap-4">
      {item.description ? <p className="text-sm text-foreground">{item.description}</p> : null}
      <CapabilityLine facts={card.capabilities} />
      <Facts rows={rows} />
      <p className="text-xs text-warn-foreground">{card.refusal}</p>
    </div>
  );
}

/**
 * The detail panel of one connector (§2.7), addressed by `item=`. A configured MCP server keeps here
 * everything its row had — Test, the last test, the held diff with Approve, the OAuth sign-in — and an
 * OpenAPI connector keeps its switches and its key. The card only summarises.
 */
export function ConnectorDetail({
  target,
  onClose,
  controls,
  autoloadOff,
  store,
  onConnectorsChanged,
  onMcpAdded,
}: {
  target: ConnectorTarget | null;
  onClose: () => void;
  controls: McpControls;
  autoloadOff: boolean;
  /** Where the OpenAPI connectors are stored, as the server reports it. */
  store: string | null;
  onConnectorsChanged: () => void;
  onMcpAdded: (name: string, test: boolean) => void;
}) {
  const t = useT();
  if (!target) return <CatalogDetail open={false} onClose={onClose} title="">{null}</CatalogDetail>;

  if (target.kind === "mcp") {
    const s = target.server;
    return (
      <CatalogDetail open onClose={onClose} title={s.name} subtitle={`${t("connectorsUi.kind.mcp")} · ${serverCommand(s)}`}>
        {autoloadOff ? (
          <p className="rounded-xl2 bg-surface-2 px-3 py-2 text-xs text-muted-foreground ring-1 ring-hairline">
            {t("mcp.autoloadOff")}
          </p>
        ) : null}
        <div className="-mx-4">
          <McpServerRow server={s} controls={controls} onRemoved={onClose} />
        </div>
      </CatalogDetail>
    );
  }
  if (target.kind === "openapi") {
    const c = target.connector;
    return (
      <CatalogDetail open onClose={onClose} title={c.name} subtitle={t("connectorsUi.kind.openapi")}>
        <p className="text-xs text-muted-foreground">{t("connectors.intro")}</p>
        <div className="-mx-4">
          <ConnectorRow connector={c} onChanged={onConnectorsChanged} onRemoved={onClose} />
        </div>
        {store ? <p className="text-xs text-muted-foreground">{t("connectors.file", { path: store })}</p> : null}
      </CatalogDetail>
    );
  }
  if (target.kind === "curated") {
    const e = target.entry;
    return (
      <CatalogDetail open onClose={onClose} title={e.label} subtitle={`${t("connectorsUi.kind.mcp")} · ${e.runner}`}>
        <CuratedBody entry={e} configured={target.configured} onAdded={onMcpAdded} />
      </CatalogDetail>
    );
  }
  const item = target.item;
  return (
    <CatalogDetail open onClose={onClose} title={item.name} subtitle={t("connectorsUi.kind.public")}>
      <PublicBody item={item} />
    </CatalogDetail>
  );
}
