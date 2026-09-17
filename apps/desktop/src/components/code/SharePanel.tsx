import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Copy, Link2, Users, X } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import {
  closeNetworkShare,
  getNetworkShare,
  listShares,
  openNetworkShare,
  revokeShare,
  shareSession,
} from "@/lib/api";
import { useT } from "@/lib/i18n";
import type { ShareInfo } from "@/lib/types";

/**
 * Sharing this conversation with a second person: the links, the network door, and who is here.
 *
 * The sentence at the top is not decoration and is not softened. A guest holds a token that opens
 * exactly this conversation — and inside it can ask the agent to do anything the owner can ask,
 * in the owner's project, with the owner's tools and spend, short of answering the governance
 * cards. Nobody should make a link without having read that.
 *
 * A link is shown only when it can be opened: with the network door closed, a token exists but
 * the address does not, and a link that goes nowhere is worse than none. The door is never open
 * at launch, and the panel says which addresses it opened on rather than guessing one.
 */
export function SharePanel({
  sessionId,
  presence,
  onSharesChanged,
  onClose,
}: {
  sessionId: string;
  /** Who is watching this conversation right now, by the names they gave. */
  presence: string[];
  onSharesChanged: (count: number) => void;
  onClose: () => void;
}) {
  const t = useT();
  const qc = useQueryClient();
  const [label, setLabel] = useState("");
  const [copied, setCopied] = useState<string | null>(null);
  const [note, setNote] = useState("");

  const shares = useQuery({
    queryKey: ["code-shares", sessionId],
    queryFn: () => listShares(sessionId),
  });
  const door = useQuery({ queryKey: ["share-network"], queryFn: getNetworkShare });

  const refresh = async () => {
    // Always from the server: the app's client keeps a query fresh for thirty seconds, and a
    // fresh cache is exactly the list from before the link was minted or the door opened. Seen
    // live — the door open on a port, every link still "no address until the network is open".
    const fresh = await qc.fetchQuery({
      queryKey: ["code-shares", sessionId],
      queryFn: () => listShares(sessionId),
      staleTime: 0,
    });
    onSharesChanged(fresh.shares.length);
  };

  const mint = useMutation({
    mutationFn: () => shareSession(sessionId, label),
    onSuccess: async () => {
      setLabel("");
      setNote("");
      await refresh();
    },
    onError: (err) => setNote(err instanceof Error ? err.message : t("code.share.failed")),
  });
  const revoke = useMutation({
    mutationFn: (token: string) => revokeShare(sessionId, token),
    onSuccess: async () => {
      await refresh();
    },
  });
  const open = useMutation({
    mutationFn: () => openNetworkShare(0),
    onSuccess: async () => {
      setNote("");
      await qc.invalidateQueries({ queryKey: ["share-network"] });
      await refresh();
    },
    onError: (err) => setNote(err instanceof Error ? err.message : t("code.share.failed")),
  });
  const close = useMutation({
    mutationFn: () => closeNetworkShare(),
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: ["share-network"] });
      await refresh();
    },
  });

  async function copy(share: ShareInfo) {
    const text = share.url ?? share.token;
    try {
      await navigator.clipboard.writeText(text);
      setCopied(share.token);
      setTimeout(() => setCopied(null), 1500);
    } catch {
      setNote(t("code.share.copyFailed"));
    }
  }

  const isOpen = door.data?.open === true;
  const rows = shares.data?.shares ?? [];

  return (
    <div className="space-y-3 rounded-card border border-hairline bg-surface-2/60 p-3" data-testid="share-panel">
      <div className="flex items-center gap-2">
        <Link2 className="h-4 w-4 text-accent" />
        <h3 className="text-sm font-semibold text-foreground">{t("code.share.title")}</h3>
        <Button size="sm" variant="ghost" className="ml-auto" onClick={onClose} aria-label={t("code.share.close")}>
          <X className="h-3.5 w-3.5" />
        </Button>
      </div>

      {/* The sentence. Warn tone, because the words are the warning. */}
      <p className="text-xs text-warn-foreground" data-testid="share-warning">
        {t("code.share.warning")}
      </p>

      <section className="space-y-1.5">
        <div className="flex flex-wrap items-center gap-2 text-xs">
          <span className="font-medium text-foreground">{t("code.share.door")}</span>
          {isOpen ? (
            <>
              <span className="text-ok-foreground" data-testid="share-door-open">
                {t("code.share.doorOpen", { port: door.data?.port ?? 0 })}
              </span>
              <Button size="sm" variant="ghost" onClick={() => close.mutate()} disabled={close.isPending}>
                {t("code.share.doorClose")}
              </Button>
            </>
          ) : (
            <>
              <span className="text-muted-foreground" data-testid="share-door-closed">
                {t("code.share.doorClosed")}
              </span>
              <Button size="sm" variant="ghost" onClick={() => open.mutate()} disabled={open.isPending}>
                {t("code.share.doorOpenAction")}
              </Button>
            </>
          )}
        </div>
        {isOpen && door.data?.urls?.length ? (
          <p className="font-mono text-xs text-muted-foreground" data-testid="share-door-urls">
            {door.data.urls.join("  ·  ")}
          </p>
        ) : null}
      </section>

      <section className="space-y-1.5">
        <div className="flex flex-wrap items-center gap-2">
          <input
            className="min-w-0 flex-1 rounded-chip border border-border bg-surface px-2 py-1 text-xs text-foreground"
            placeholder={t("code.share.labelHint")}
            value={label}
            onChange={(ev) => setLabel(ev.target.value)}
            aria-label={t("code.share.labelHint")}
          />
          <Button size="sm" onClick={() => mint.mutate()} disabled={mint.isPending}>
            {t("code.share.newLink")}
          </Button>
        </div>
        {rows.length === 0 ? (
          <p className="text-xs text-muted-foreground">{t("code.share.none")}</p>
        ) : (
          <ul className="space-y-1" data-testid="share-list">
            {rows.map((share) => (
              <li key={share.token} className="flex flex-wrap items-center gap-2 text-xs">
                <span className="font-medium text-foreground">{share.label || t("code.share.unlabelled")}</span>
                {share.url ? (
                  <span className="min-w-0 flex-1 truncate font-mono text-muted-foreground" title={share.url}>
                    {share.url}
                  </span>
                ) : (
                  <span className="min-w-0 flex-1 text-muted-foreground">{t("code.share.noAddress")}</span>
                )}
                <Button size="sm" variant="ghost" onClick={() => void copy(share)} title={t("code.share.copy")}>
                  {copied === share.token ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}
                </Button>
                <Button size="sm" variant="ghost" onClick={() => revoke.mutate(share.token)} disabled={revoke.isPending}>
                  {t("code.share.revoke")}
                </Button>
              </li>
            ))}
          </ul>
        )}
      </section>

      <p className="flex items-center gap-1.5 text-xs text-muted-foreground" data-testid="share-presence">
        <Users className="h-3.5 w-3.5" />
        {presence.length ? t("code.share.here", { names: presence.join(", ") }) : t("code.share.nobody")}
      </p>
      {note ? <p className="text-xs text-bad-foreground">{note}</p> : null}
    </div>
  );
}
