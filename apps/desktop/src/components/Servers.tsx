import { useId, useState } from "react";
import { Check, Plus, Trash2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { useT } from "@/lib/i18n";
import {
  LOCAL,
  type Server,
  active,
  handshake,
  normaliseBase,
  rejectReason,
  saveServers,
  servers,
  setActive,
} from "@/lib/server";

/**
 * Which Chimera this app is talking to.
 *
 * The app has only ever talked to the backend it was served from, which is right for the sidecar
 * it starts and leaves someone running Chimera on their own VPS with no way in. This is the other
 * case, and it is deliberately not a convenience: a remote is refused unless it is HTTPS and has a
 * token, because both failures are invisible from here — a token in a header over plain HTTP is
 * broadcast to every hop, and an instance with no token is an agent anyone who finds the address
 * can run commands through.
 *
 * Test and Use are separate on purpose. Testing tells you whether the address, the token and the
 * origin allowance are right while you can still read the screen; switching reloads the window,
 * and a failed switch would leave you looking at a broken app trying to remember what you typed.
 */
export function Servers() {
  const t = useT();
  const [list, setList] = useState<Server[]>(servers);
  const [current, setCurrent] = useState(active().id);
  const [draft, setDraft] = useState<{ name: string; url: string; token: string } | null>(null);
  const [note, setNote] = useState<{ kind: "ok" | "warn" | "bad"; text: string } | null>(null);
  const [busy, setBusy] = useState(false);

  const persist = (next: Server[]) => {
    setList(next);
    saveServers(next);
  };

  /**
   * Switching reloads the window, and that is the correctness fix rather than laziness.
   *
   * Every screen is holding data fetched from the previous server — sessions, runs, the board, the
   * config. Swapping the base URL under them would leave the old server's answers on screen under
   * the new server's name, which is the one mistake this feature must never make: acting on the
   * wrong Chimera because the screen said the right one.
   */
  const use = (id: string) => {
    setActive(id);
    setCurrent(id);
    window.location.reload();
  };

  const test = async (url: string, token: string) => {
    const reason = rejectReason(url, token);
    if (reason) {
      setNote({ kind: "bad", text: t(`server.err${reason[0]?.toUpperCase()}${reason.slice(1)}`) });
      return null;
    }
    setBusy(true);
    try {
      const base = normaliseBase(url);
      const r = await handshake(base, token);
      if (!r.ok) {
        const text =
          r.reason === "unreachable"
            ? t("server.errUnreachable", { origin: window.location.origin })
            : t(`server.err${r.reason[0]?.toUpperCase()}${r.reason.slice(1)}`);
        setNote({ kind: "bad", text });
        return null;
      }
      setNote(
        r.sameVersion
          ? { kind: "ok", text: t("server.ok", { version: r.version }) }
          : { kind: "warn", text: t("server.skew", { server: r.version, app: r.appVersion }) },
      );
      return base;
    } finally {
      setBusy(false);
    }
  };

  const save = async () => {
    if (!draft) return;
    const base = await test(draft.url, draft.token);
    if (!base) return;
    const server: Server = {
      id: `s${Date.now().toString(36)}`,
      name: draft.name.trim() || new URL(base).host,
      baseUrl: base,
      token: draft.token.trim(),
    };
    persist([...list, server]);
    setDraft(null);
  };

  const rows: Server[] = [{ ...LOCAL, name: t("server.local") }, ...list];

  return (
    <div className="grid gap-4">
      <div>
        <h3 className="text-sm font-medium">{t("server.title")}</h3>
        <p className="mt-1 max-w-prose text-xs text-muted-foreground">{t("server.body")}</p>
      </div>

      <ul className="grid gap-2">
        {rows.map((s) => (
          <li
            key={s.id}
            className="flex items-center gap-3 rounded-chip border border-hairline px-3 py-2"
          >
            <div className="min-w-0 flex-1">
              <p className="truncate text-sm">{s.name}</p>
              <p className="truncate font-mono text-xs text-muted-foreground">
                {s.baseUrl || t("server.localBody")}
              </p>
            </div>
            {s.id === current ? (
              <span className="flex items-center gap-1 text-xs text-ok">
                <Check className="h-3 w-3" aria-hidden />
                {t("server.inUse")}
              </span>
            ) : (
              <Button size="sm" variant="ghost" onClick={() => use(s.id)}>
                {t("server.use")}
              </Button>
            )}
            {s.baseUrl && (
              <Button
                size="sm"
                variant="ghost"
                aria-label={t("server.remove")}
                onClick={() => {
                  // Removing the one in use falls back to local rather than to nothing: the screen
                  // that would fix "pointing at a server that no longer exists" is behind requests
                  // to that server.
                  if (s.id === current) use(LOCAL.id);
                  persist(list.filter((x) => x.id !== s.id));
                }}
              >
                <Trash2 className="h-3.5 w-3.5" aria-hidden />
              </Button>
            )}
          </li>
        ))}
      </ul>

      {note && (
        <p
          role="status"
          className={
            note.kind === "ok"
              ? "text-xs text-ok"
              : note.kind === "warn"
                ? "text-xs text-warn-foreground"
                : "text-xs text-bad"
          }
        >
          {note.text}
        </p>
      )}

      {draft ? (
        <div className="grid gap-2 rounded-chip border border-hairline p-3">
          <Field label={t("server.name")}>
            {(a) => (
              <input
                {...a}
                className="w-full bg-transparent text-sm outline-none"
                value={draft.name}
                onChange={(e) => setDraft({ ...draft, name: e.target.value })}
              />
            )}
          </Field>
          <Field label={t("server.url")}>
            {(a) => (
              <input
                {...a}
                className="w-full bg-transparent font-mono text-sm outline-none"
                placeholder="https://chimera.exemplo.com"
                value={draft.url}
                onChange={(e) => setDraft({ ...draft, url: e.target.value })}
              />
            )}
          </Field>
          <Field label={t("server.token")} hint={t("server.tokenHint")}>
            {(a) => (
              <input
                {...a}
                type="password"
                className="w-full bg-transparent font-mono text-sm outline-none"
                value={draft.token}
                onChange={(e) => setDraft({ ...draft, token: e.target.value })}
              />
            )}
          </Field>
          <div className="flex gap-2">
            <Button size="sm" variant="outline" disabled={busy} onClick={() => void test(draft.url, draft.token)}>
              {t("server.test")}
            </Button>
            <Button size="sm" disabled={busy} onClick={() => void save()}>
              {t("server.save")}
            </Button>
            <Button size="sm" variant="ghost" onClick={() => { setDraft(null); setNote(null); }}>
              {t("common.cancel")}
            </Button>
          </div>
        </div>
      ) : (
        <div>
          <Button
            size="sm"
            variant="outline"
            onClick={() => { setDraft({ name: "", url: "", token: "" }); setNote(null); }}
          >
            <Plus className="mr-1 h-3.5 w-3.5" aria-hidden />
            {t("server.add")}
          </Button>
        </div>
      )}
    </div>
  );
}

/**
 * A labelled field whose hint is a DESCRIPTION, not part of its name.
 *
 * The obvious version puts the hint inside the `<label>`, and then a screen reader announces the
 * token box as "Token The CHIMERA_SERVER_TOKEN of that instance" — the name becomes a paragraph.
 * The same defect was found and fixed in the agent registry screen while writing its tests; I
 * wrote the warning here and then reproduced it anyway, which is why the hint now lives outside
 * the label and is wired with `aria-describedby`.
 */
function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: (props: { id: string; "aria-describedby"?: string }) => React.ReactNode;
}) {
  const id = useId();
  const hintId = `${id}-hint`;
  return (
    <div className="grid gap-1">
      <label htmlFor={id} className="text-xs text-muted-foreground">
        {label}
      </label>
      {children(hint ? { id, "aria-describedby": hintId } : { id })}
      {hint && (
        <span id={hintId} className="text-xs text-muted-foreground/70">
          {hint}
        </span>
      )}
    </div>
  );
}
