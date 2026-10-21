import { useContext, useEffect, useId, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, KeyRound, Loader2, Search, Trash2 } from "lucide-react";
import {
  addPoolKey,
  addBrowserTrustedSite,
  getBrowserTrustedSites,
  getBrowserLink,
  removeBrowserTrustedSite,
  stopBrowserLink,
  unpairBrowserLink,
  issueBrowserPairCode,
  getConfig,
  getDoctor,
  getInstructions,
  getMessaging,
  getCompletionStats,
  getSuggestionStats,
  getOllamaModels,
  getSandboxState,
  patchConfig,
  putInstructions,
  removePoolKey,
  startMessaging,
  stopMessaging,
} from "@/lib/api";
import { Button } from "@/components/ui/button";
import { ErrorState } from "@/components/ui/async";

import { Switch } from "@/components/ui/switch";
import {
  CodeFontSelect,
  AccentPicker,
  ContrastSelect,
  MotionSelect,
  TintSelect,
  TextSizeSelect,
  ThemeSelect,
  TranscriptWidthSelect,
  TranscriptViewSelect,
  UiZoomSelect,
  UiFontSelect,
} from "@/components/AppearanceControls";
import { Tabs, TabPanel, type TabItem } from "@/components/ui/tabs";
import { Connections } from "@/components/Connections";
import { Connectors } from "@/components/Connectors";
import { Skills } from "@/components/Skills";
import { Plugins } from "@/components/plugins/Plugins";
import { Governance } from "@/components/Governance";
import { Decisions } from "@/components/Decisions";
import { Usage } from "@/components/Usage";
import { SystemOneCard } from "@/components/SystemOneCard";
import { KeepAwakeCard } from "@/components/KeepAwakeCard";
import { DeferSavingNote } from "@/components/DeferSavingNote";
import { FoldersCard } from "@/components/FoldersCard";
import { VoiceCard } from "@/components/VoiceCard";
import { NotificationsCard } from "@/components/NotificationsCard";
import { ShellCard } from "@/components/ShellCard";
import { ConversationsCard } from "@/components/ConversationsCard";
import { WeeklyReviewRow } from "@/components/WeeklyReviewRow";
import { StorageCard } from "@/components/StorageCard";
import { DiagnosticsCard } from "@/components/DiagnosticsCard";
import { KeyVaultMove } from "@/components/KeyVaultMove";
import { ModelPicker } from "@/components/code/ModelPicker";
import { LANGS, useI18n, useT } from "@/lib/i18n";
import { isBrowserSite, splitBrowserSites } from "@/lib/browser-sites";
import type {
  AgentIdentity,
  AppConfig,
  DoctorInfo,
  PoolCfg,
  ProviderCfg,
  SandboxState,
} from "@/lib/types";
import { Card, PinnedContext, Row, RowLabelContext, Toggle } from "@/components/settings/rows";
import { ResponseStyleNote } from "@/components/ResponseStyleNote";
import { SettingsSearchResults } from "@/components/settings/SettingsSearch";
import { readWorkspace, writeWorkspace } from "@/lib/workspace";

/**
 * Who the agent is — the one thing this screen could not say.
 *
 * The three places that looked like they already did this did not: `profile.json` has no reader in
 * the API, persona memory facts are retrieved by relevance so a standing instruction applied only on
 * the turns whose wording matched it, and the unconditional preamble slot was filled by two paths
 * the app never takes. So the Profile screen showed a profile the agent never applied, and the
 * language selector below changed the interface while the agent kept answering in English.
 *
 * Saved as one record rather than three settings because it is one idea, and because the fields are
 * already the shape a second agent would need.
 */
function IdentityCard() {
  const t = useT();
  const { lang } = useI18n();
  const qc = useQueryClient();
  const saved = useQuery({
    queryKey: ["instructions"],
    queryFn: getInstructions,
  });
  const [draft, setDraft] = useState<AgentIdentity | null>(null);

  const mutation = useMutation({
    mutationFn: putInstructions,
    onSuccess: (stored) => {
      // The server's record, not the draft: the free text is capped, and someone who pasted more
      // than the budget has to see what the agent will actually read.
      qc.setQueryData(["instructions"], stored);
      setDraft(null);
    },
  });

  const current = draft ??
    saved.data ?? { name: "", language: "", instructions: "" };
  const dirty =
    draft !== null && JSON.stringify(draft) !== JSON.stringify(saved.data);
  const edit = (patch: Partial<AgentIdentity>) =>
    setDraft({ ...current, ...patch });
  const uiLanguage = LANGS.find((l) => l.code === lang)?.label ?? "";

  return (
    <Card title={t("settings.card.agent")}>
      <Row
        label={t("settings.row.agentName")}
        hint={t("settings.hint.agentName")}
      >
        <input
          className={inputCls}
          value={current.name}
          placeholder="Chimera"
          aria-label={t("settings.row.agentName")}
          onChange={(e) => edit({ name: e.target.value })}
        />
      </Row>
      <Row
        label={t("settings.row.agentLanguage")}
        hint={t("settings.hint.agentLanguage")}
      >
        <input
          className={inputCls}
          value={current.language}
          placeholder={t("settings.placeholder.agentLanguage")}
          aria-label={t("settings.row.agentLanguage")}
          onChange={(e) => edit({ language: e.target.value })}
        />
        {/* One click for the answer the app already knows. Not applied automatically: an agent that
            silently switched language because someone changed the interface would be a surprise,
            and the two are genuinely separate choices — a Brazilian reading English docs may want
            exactly that split. */}
        <Button
          size="sm"
          variant="outline"
          disabled={!uiLanguage || current.language === uiLanguage}
          onClick={() => edit({ language: uiLanguage })}
        >
          {t("settings.action.useUiLanguage")}
        </Button>
      </Row>
      <div className="flex flex-col gap-2 px-4 py-3">
        <div>
          <div className="text-sm font-medium">
            {t("settings.row.agentInstructions")}
          </div>
          <div className="text-xs text-muted-foreground">
            {t("settings.hint.agentInstructions")}
          </div>
        </div>
        <textarea
          className="field min-h-32 w-full p-2.5 text-sm"
          value={current.instructions}
          placeholder={t("settings.placeholder.agentInstructions")}
          aria-label={t("settings.row.agentInstructions")}
          onChange={(e) => edit({ instructions: e.target.value })}
        />
        {/* Said where it is decided, not in a tooltip. Someone writing "you may run any command"
            here and then watching the agent refuse deserves to have been told in the same breath. */}
        <p className="text-xs text-muted-foreground">
          {t("settings.hint.agentNoGrant")}
        </p>
        <div className="flex items-center gap-2">
          <Button
            size="sm"
            disabled={!dirty || mutation.isPending}
            onClick={() => mutation.mutate(current)}
          >
            {t("common.save")}
          </Button>
          {dirty && (
            <span className="text-xs text-warn-foreground">{t("settings.unsaved")}</span>
          )}
        </div>
      </div>
    </Card>
  );
}

/** How much the agent may do — the three controls that decide it, together.
 *
 * They were spread across a dead component, a hardcoded pair in the Code screen, and an env var that
 * `PATCH /api/config` refused. Reading them separately never told anyone the answer, which is why
 * they render as one card: "what may my right hand do" is one question.
 *
 * Empty reach/approval mean "state nothing", which is not the same as stating a permissive posture:
 * unset, the conversation's own posture is the only one in force. Set either and it becomes a floor
 * the request cannot raise — the same rule as the tool denylist, for the same reason.
 */
function AutonomyCard({
  c,
  save,
}: {
  c: AppConfig;
  save: (u: Record<string, string>) => void;
}) {
  const t = useT();
  const [confirmingHostExec, setConfirmingHostExec] = useState(false);

  return (
    <Card title={t("settings.card.autonomy")}>
      <Row
        label={t("settings.row.reach")}
        hint={t("settings.hint.reach")}
        env="CHIMERA_REACH"
      >
        <Select
          value={c.autonomy.reach || "unset"}
          options={["unset", "read_only", "workspace", "workspace_shell"]}
          render={(v) =>
            v === "unset"
              ? t("settings.value.unset")
              : t(`code.posture.reach.${v}`)
          }
          onChange={(v) => save({ CHIMERA_REACH: v === "unset" ? "" : v })}
        />
      </Row>
      <Row
        label={t("settings.row.approval")}
        hint={t("settings.hint.approval")}
        env="CHIMERA_APPROVAL"
      >
        <Select
          value={c.autonomy.approval || "unset"}
          options={["unset", "always", "suspicious", "never"]}
          render={(v) =>
            v === "unset"
              ? t("settings.value.unset")
              : t(`code.posture.approval.${v}`)
          }
          onChange={(v) => save({ CHIMERA_APPROVAL: v === "unset" ? "" : v })}
        />
      </Row>
      <Row
        label={t("settings.row.hostExec")}
        hint={t("settings.hint.hostExec")}
        env="CHIMERA_HOST_EXEC"
      >
        <Select
          value={c.autonomy.host_exec}
          options={["ask", "deny", "allow"]}
          render={(v) => t(`settings.value.${v}`)}
          onChange={(v) => {
            // `allow` is the only value here that removes a human from the loop on the user's own
            // machine, so it does not get to be the third entry in a silent dropdown. Everything
            // else applies straight away — asking to confirm a narrowing would train people to
            // click through the one confirmation that matters.
            if (v === "allow") setConfirmingHostExec(true);
            else {
              setConfirmingHostExec(false);
              save({ CHIMERA_HOST_EXEC: v });
            }
          }}
        />
      </Row>
      {confirmingHostExec && (
        <div className="flex flex-col gap-2 px-4 py-3">
          <p className="text-xs text-bad-foreground">{t("settings.hostExec.warning")}</p>
          <div className="flex items-center gap-2">
            <Button
              size="sm"
              variant="outline"
              onClick={() => {
                save({ CHIMERA_HOST_EXEC: "allow" });
                setConfirmingHostExec(false);
              }}
            >
              {t("settings.hostExec.confirm")}
            </Button>
            <Button
              size="sm"
              variant="ghost"
              onClick={() => setConfirmingHostExec(false)}
            >
              {t("common.cancel")}
            </Button>
          </div>
        </div>
      )}
      {/* How hard a dollar ceiling the person typed holds (owner's decision, 2026-10-05). Off, as
          shipped: the last call admitted may pass it by its own worst case. On, a call starts only
          if its worst case still fits, and one that cannot be priced is refused. Named "strict" on
          the row so the refusal it can cause is never a surprise; a run with no ceiling is
          untouched either way, since limits are warnings. The desktop bridge cannot write it. */}
      <Row
        label={t("settings.row.strictSpendCap")}
        hint={t("settings.hint.strictSpendCap")}
        env="CHIMERA_STRICT_SPEND_CAP"
      >
        <Toggle
          on={c.spend?.strict_cap ?? false}
          onChange={(v) => save({ CHIMERA_STRICT_SPEND_CAP: String(v) })}
        />
      </Row>
      {/* The agent's `open_pull_request` (study 29, P8.1). Off unless the owner turns it on, and
          worded as a warning: it widens where the owner's code can go. Each pull request still asks
          on a card whatever this row says, so the switch decides whether the agent may PROPOSE one,
          never whether it may open one unasked. The desktop bridge cannot write it. */}
      <Row
        label={t("settings.row.pullRequests")}
        hint={t("settings.hint.pullRequests")}
        warn
        applies={c.applies?.CHIMERA_PULL_REQUESTS}
        env="CHIMERA_PULL_REQUESTS"
      >
        <Toggle
          on={c.autonomy.pull_requests ?? false}
          onChange={(v) => save({ CHIMERA_PULL_REQUESTS: String(v) })}
        />
      </Row>
      {/* The owner's lifecycle hooks (owner's decision, 2026-10-05; docs/hooks-threat-model.md).
          Off by default. On, a hook can only deny, ask or annotate — never allow — so the row is
          not a warning; the host row below it is, because it lets the owner's shell hooks leave the
          sandbox. The desktop bridge can write neither. */}
      <Row
        label={t("settings.row.hooks")}
        hint={t("settings.hint.hooks")}
        applies={c.applies?.CHIMERA_HOOKS}
        env="CHIMERA_HOOKS"
      >
        <Toggle
          on={c.autonomy.hooks ?? false}
          onChange={(v) => save({ CHIMERA_HOOKS: String(v) })}
        />
      </Row>
      <Row
        label={t("settings.row.hooksHostExec")}
        hint={t("settings.hint.hooksHostExec")}
        warn
        applies={c.applies?.CHIMERA_HOOKS_HOST_EXEC}
        env="CHIMERA_HOOKS_HOST_EXEC"
      >
        <Toggle
          on={c.autonomy.hooks_host_exec ?? false}
          onChange={(v) => save({ CHIMERA_HOOKS_HOST_EXEC: String(v) })}
        />
      </Row>
      <Row
        label={t("settings.row.governance")}
        hint={t("settings.hint.governance")}
        env="CHIMERA_GOVERNANCE"
      >
        <Select
          value={c.autonomy.governance || "off"}
          options={["off", "observe", "enforce"]}
          // Spelled out, not composed: the dead-key gate reads the source for literal key
          // strings, so an interpolated key is one it cannot see — and would report as unused.
          render={(v) =>
            v === "enforce"
              ? t("settings.value.governance.enforce")
              : v === "observe"
                ? t("settings.value.governance.observe")
                : t("settings.value.governance.off")
          }
          // No confirmation on the way up, deliberately, for the reason written on `host_exec`
          // above: asking to confirm a NARROWING trains people to click through the one
          // confirmation that matters. What the two modes cost is stated below instead, as a
          // measurement, so the decision is made with the number rather than after it.
          onChange={(v) => save({ CHIMERA_GOVERNANCE: v })}
        />
      </Row>
      {(c.autonomy.governance || "off") !== "off" && (
        <p className="px-4 pb-3 text-xs text-muted-foreground">
          {t("settings.governance.cost")}
        </p>
      )}
      <Row
        label={t("settings.row.approvalWebhook")}
        hint={t("settings.hint.approvalWebhook")}
        env="CHIMERA_APPROVAL_WEBHOOK"
      >
        <TextField
          // Never the value. The URL is a credential — whoever holds it can post into that
          // channel — so the API reports only whether one is saved, and the field says which.
          value=""
          placeholder={
            c.autonomy.approval_webhook_set
              ? t("settings.approvalWebhook.saved")
              : "https://…/webhook"
          }
          onSave={(v) => save({ CHIMERA_APPROVAL_WEBHOOK: v })}
        />
      </Row>
      <Row
        label={t("settings.row.egressAllow")}
        hint={t("settings.hint.egressAllow")}
        env="CHIMERA_EGRESS_ALLOW"
      >
        <TextField
          // The values, not a mask: this is the owner's own statement about where their agent may
          // fetch from, and a list that cannot be read back is a list that cannot be corrected.
          value={(c.autonomy.egress_allow ?? []).join(", ")}
          placeholder="api.github.com, docs.example"
          onSave={(v) => save({ CHIMERA_EGRESS_ALLOW: v })}
        />
      </Row>
      {(c.autonomy.denied_tools ?? []).length > 0 && (
        <Row
          label={t("settings.row.deniedTools")}
          hint={t("settings.hint.deniedTools")}
          env="CHIMERA_TOOL_DENYLIST"
        >
          <span className="max-w-56 truncate font-mono text-xs text-muted-foreground">
            {(c.autonomy.denied_tools ?? []).join(", ")}
          </span>
        </Row>
      )}
    </Card>
  );
}

/** Study 30's opt-in governance rules and its one audit record — every one off as shipped.
 *
 * A card of its own rather than more rows in Autonomy: that card answers "what may my right hand
 * do", and these do not change what a run may reach. Each one adds a question, arms a run or keeps
 * a record, and each ships off because its measurement did not recommend it — so, like the
 * Experimental card, every hint says what was measured rather than what the rule promises. Before
 * this card, four of them existed only in `.env` and the other two could be saved but not read back.
 *
 * Every row is owner-only (`bridge_routes.GUARD_SETTINGS`): the desktop bridge can switch none of
 * them, in either direction. A server that predates the `governance_audit` block reads as all off,
 * which is what it does.
 */
function GovernanceAuditCard({
  c,
  save,
}: {
  c: AppConfig;
  save: (u: Record<string, string>) => void;
}) {
  const t = useT();
  const g = c.governance_audit;
  const deadline = g?.band_deadline_s;
  return (
    <Card title={t("settings.card.governanceAudit")}>
      <p className="px-4 py-2.5 text-xs text-muted-foreground">
        {t("settings.governanceAudit.intro")}
      </p>
      {/* A record, not a rule: it blocks nothing, and the hint says so first. What reaches the file
          is digests and the model name — `chimera/governance/reconcile.py` — never a body or a key. */}
      <Row
        label={t("settings.row.wireLog")}
        hint={t("settings.hint.wireLog")}
        note={
          <div className="text-xs text-muted-foreground">
            {t("settings.governanceAudit.docs")}
          </div>
        }
        env="CHIMERA_WIRE_LOG"
      >
        <Toggle
          on={g?.wire_log ?? false}
          onChange={(v) => save({ CHIMERA_WIRE_LOG: String(v) })}
        />
      </Row>
      {/* Empty is no deadline, the shipped behaviour. The server refuses zero and negatives before
          writing (the setting is `gt=0`, so a saved zero would stop the app at the next read). Shown
          with the band's own state, because a deadline on a band that is off does nothing. */}
      <Row
        label={t("settings.row.bandDeadline")}
        hint={t("settings.hint.bandDeadline")}
        note={
          g?.band_on ? undefined : (
            <div className="text-xs text-warn-foreground">
              {t("settings.bandDeadline.bandOff")}
            </div>
          )
        }
        applies={c.applies?.CHIMERA_GOVERNANCE_BAND_DEADLINE_S}
        env="CHIMERA_GOVERNANCE_BAND_DEADLINE_S"
      >
        <TextField
          value={deadline != null ? String(deadline) : ""}
          placeholder={t("settings.placeholder.bandDeadline")}
          onSave={(v) => save({ CHIMERA_GOVERNANCE_BAND_DEADLINE_S: v.trim() })}
        />
      </Row>
      <Row
        label={t("settings.row.exfilHostPath")}
        hint={t("settings.hint.exfilHostPath")}
        applies={c.applies?.CHIMERA_EXFIL_HOST_PATH}
        env="CHIMERA_EXFIL_HOST_PATH"
      >
        <Toggle
          on={g?.exfil_host_path ?? false}
          onChange={(v) => save({ CHIMERA_EXFIL_HOST_PATH: String(v) })}
        />
      </Row>
      <Row
        label={t("settings.row.shellFetchGuard")}
        hint={t("settings.hint.shellFetchGuard")}
        applies={c.applies?.CHIMERA_SHELL_FETCH_GUARD}
        env="CHIMERA_SHELL_FETCH_GUARD"
      >
        <Toggle
          on={g?.shell_fetch_guard ?? false}
          onChange={(v) => save({ CHIMERA_SHELL_FETCH_GUARD: String(v) })}
        />
      </Row>
      <Row
        label={t("settings.row.armOnRecalledLessons")}
        hint={t("settings.hint.armOnRecalledLessons")}
        env="CHIMERA_ARM_ON_RECALLED_LESSONS"
      >
        <Toggle
          on={g?.arm_on_recalled_lessons ?? false}
          onChange={(v) => save({ CHIMERA_ARM_ON_RECALLED_LESSONS: String(v) })}
        />
      </Row>
      {/* Published verdict FAIL, and neither harness turned it on — the hint says both, so "on" is
          never read as a measured improvement. */}
      <Row
        label={t("settings.row.ropeLite")}
        hint={t("settings.hint.ropeLite")}
        applies={c.applies?.CHIMERA_TAINT_ROPE_LITE}
        env="CHIMERA_TAINT_ROPE_LITE"
      >
        <Toggle
          on={g?.taint_rope_lite ?? false}
          onChange={(v) => save({ CHIMERA_TAINT_ROPE_LITE: String(v) })}
        />
      </Row>
    </Card>
  );
}

const inputCls = "field h-8 w-56 px-2.5 text-sm";

function TextField({
  value,
  onSave,
  placeholder,
}: {
  value: string;
  onSave: (v: string) => void;
  placeholder?: string;
}) {
  const t = useT();
  const [v, setV] = useState(value);
  const dirty = v !== value;
  return (
    <>
      <input
        className={inputCls}
        value={v}
        placeholder={placeholder}
        // Named from its Row, the last of the three controls here that was not. Unlabelled, every
        // text field on this screen announced as "edit text" — the value with no statement of what
        // it is the value OF, which for a field holding a model slug is the whole content.
        aria-label={useContext(RowLabelContext)}
        onChange={(e) => setV(e.target.value)}
      />
      <Button size="sm" disabled={!dirty} onClick={() => onSave(v)}>
        {t("common.save")}
      </Button>
    </>
  );
}

/**
 * The browser's site list, edited one entry at a time. Each add and each remove is one PATCH of the
 * whole list, the way the text field it replaced saved it; the syntax check is `parse_sites`'s
 * (`@/lib/browser-sites`), and the server, which refuses anything it misses, stays the authority.
 */
function BrowserLinkSettings() {
  const t = useT();
  const qc = useQueryClient();
  const status = useQuery({ queryKey: ["browser-link"], queryFn: getBrowserLink, refetchInterval: 5000 });
  const refresh = () => void qc.invalidateQueries({ queryKey: ["browser-link"] });
  const [busy, setBusy] = useState(false);
  const [pairingCode, setPairingCode] = useState<string | null>(null);
  const pair = async () => {
    setBusy(true);
    try {
      const result = await issueBrowserPairCode();
      setPairingCode(result.code);
      refresh();
    } finally { setBusy(false); }
  };
  const stop = async () => {
    setBusy(true);
    try { await stopBrowserLink(); refresh(); } finally { setBusy(false); }
  };
  const unpair = async () => {
    if (!window.confirm(t("settings.browserLink.unpairConfirm"))) return;
    setBusy(true);
    try { await unpairBrowserLink(); refresh(); } finally { setBusy(false); }
  };
  const state = status.data?.connected ? t("settings.browserLink.connected") : status.data?.paired ? t("settings.browserLink.disconnected") : t("settings.browserLink.unpaired");
  return (
    <Row label={t("settings.row.browserLink")} hint={t("settings.hint.browserLink")}>
      <div className="flex flex-col gap-2">
        <p>{status.isError ? t("settings.browserLink.unavailable") : state}</p>
        {status.data?.paired ? <p>{status.data.key_store === "vault" ? t("settings.browserLink.keyVault") : t("settings.browserLink.keyFile")}</p> : null}
        {!status.data?.paired ? <p>{t("settings.browserLink.pairCode")}</p> : null}
        {pairingCode ? <p>{t("settings.browserLink.pairingCode", { code: pairingCode, seconds: "120" })}</p> : null}
        {!status.data?.paired ? <p className="max-w-prose text-xs text-muted-foreground">{t("settings.browserLink.install")} <code>python scripts/register_chrome_host.py &lt;ID-da-extensão&gt; --apply</code></p> : null}
        <div className="flex gap-2">
          <Button size="sm" variant="outline" disabled={busy} onClick={refresh}>{t("settings.browserLink.refresh")}</Button>
          {!status.data?.paired ? <Button size="sm" variant="outline" disabled={busy} onClick={() => void pair()}>{t("settings.browserLink.pair")}</Button> : null}
          {status.data?.paired ? <span className="sr-only">{t("settings.browserLink.paired")}</span> : null}
          {status.data?.connected ? <Button size="sm" variant="outline" disabled={busy} onClick={() => void stop()}>{t("settings.browserLink.stop")}</Button> : null}
          {status.data?.paired ? <Button size="sm" variant="outline" disabled={busy} onClick={() => void unpair()}>{t("settings.browserLink.unpair")}</Button> : null}
        </div>
      </div>
    </Row>
  );
}

function BrowserTrustedSites() {
  const t = useT();
  const qc = useQueryClient();
  const query = useQuery({ queryKey: ["browser-trusted-sites"], queryFn: getBrowserTrustedSites });
  const [entry, setEntry] = useState("");
  const [invalid, setInvalid] = useState(false);
  const [busy, setBusy] = useState(false);
  const sites = query.data?.trusted_sites ?? [];
  const mutate = async (host: string, remove = false) => {
    setBusy(true);
    try {
      const result = remove ? await removeBrowserTrustedSite(host) : await addBrowserTrustedSite(host);
      qc.setQueryData(["browser-trusted-sites"], result);
      setEntry("");
      setInvalid(false);
    } catch {
      setInvalid(true);
    } finally {
      setBusy(false);
    }
  };
  const add = () => {
    const hosts = splitBrowserSites(entry);
    if (hosts.length !== 1 || !isBrowserSite(hosts[0]) || hosts[0].startsWith("*.")) {
      setInvalid(true);
      return;
    }
    void mutate(hosts[0]);
  };
  return (
    <Row label={t("settings.row.browserTrustedSites")} hint={t("settings.hint.browserTrustedSites")} warn>
      <div className="flex flex-col gap-2">
        {query.isError ? <ErrorState error={query.error} onRetry={() => void query.refetch()} /> : null}
        {sites.length ? (
          <ul className="flex flex-wrap gap-2" aria-label={t("settings.row.browserTrustedSites")}>
            {sites.map((site) => (
              <li key={site} className="flex items-center gap-1 rounded-md border border-hairline px-2 py-1 text-sm">
                <span>{site}</span>
                <Button size="sm" variant="ghost" disabled={busy} aria-label={`${t("settings.browserTrustedSites.remove")} ${site}`} onClick={() => void mutate(site, true)}>
                  <Trash2 aria-hidden="true" />
                </Button>
              </li>
            ))}
          </ul>
        ) : <p className="text-xs text-muted-foreground">{t("settings.browserTrustedSites.empty")}</p>}
        <div className="flex items-center gap-2">
          <input className={inputCls} aria-label={t("settings.row.browserAddTrustedSite")} value={entry} placeholder="shop.example.com" aria-invalid={invalid} onChange={(event) => { setEntry(event.target.value); setInvalid(false); }} onKeyDown={(event) => { if (event.key === "Enter") { event.preventDefault(); add(); } }} />
          <Button size="sm" disabled={busy || !entry.trim()} onClick={add}>{t("settings.browserTrustedSites.add")}</Button>
        </div>
        {invalid ? <p role="alert" className="text-xs text-danger">{t("settings.browserTrustedSites.invalid")}</p> : null}
      </div>
    </Row>
  );
}

function BrowserSites({ sites, onSave }: { sites: string[]; onSave: (value: string) => void }) {
  const t = useT();
  const [entry, setEntry] = useState("");
  const [invalid, setInvalid] = useState(false);
  const append = () => {
    const typed = splitBrowserSites(entry);
    if (!typed.length) return;
    if (!typed.every(isBrowserSite)) {
      setInvalid(true);
      return;
    }
    const next = [...sites];
    for (const site of typed) if (!next.includes(site)) next.push(site);
    onSave(next.join(", "));
    setEntry("");
    setInvalid(false);
  };
  const remove = (site: string) => onSave(sites.filter((item) => item !== site).join(", "));
  return (
    <div className="flex flex-col gap-2">
      {sites.length ? (
        <ul className="flex flex-wrap gap-2" aria-label={t("settings.row.browserSites")}>
          {sites.map((site) => (
            <li key={site} className="flex items-center gap-1 rounded-md border border-hairline px-2 py-1 text-sm">
              <span>{site}</span>
              <Button
                size="sm"
                variant="ghost"
                aria-label={t("settings.row.browserRemoveSite", { site })}
                onClick={() => remove(site)}
              >
                <Trash2 aria-hidden="true" />
              </Button>
            </li>
          ))}
        </ul>
      ) : (
        <p className="text-xs text-muted-foreground">{t("settings.browserSites.empty")}</p>
      )}
      <div className="flex items-center gap-2">
        <input
          className={inputCls}
          aria-label={t("settings.row.browserAddSite")}
          value={entry}
          placeholder="github.com, *.github.com"
          aria-invalid={invalid}
          onChange={(event) => {
            setEntry(event.target.value);
            setInvalid(false);
          }}
          onKeyDown={(event) => {
            if (event.key === "Enter") {
              event.preventDefault();
              append();
            }
          }}
        />
        <Button size="sm" disabled={!entry.trim()} onClick={append}>
          {t("settings.row.browserAddSite")}
        </Button>
      </div>
      {invalid ? (
        <p role="alert" className="text-xs text-danger">
          {t("settings.browserSites.invalid")}
        </p>
      ) : null}
    </div>
  );
}

/**
 * The models the configured Ollama actually has pulled.
 *
 * `ProviderPicker` makes this argument for external agents and it is the same argument here:
 * availability is settled on the machine the sidecar runs on, and a list shipped in the bundle would
 * offer a tag that is not pulled. That failure does not arrive at save time — it arrives on the first
 * call, mid-run, as a 404 from a server the user believed was ready, which is the worst moment and
 * the least legible one. The URL is already stored and already handed to LiteLLM, so asking it is one
 * request.
 *
 * Where this departs from `ProviderPicker` is what happens when the answer is nothing, and it is the
 * whole reason this renders a sentence instead of disappearing: "Ollama is not running" and "Ollama
 * is running with nothing pulled" have opposite remedies, and an empty dropdown states neither. It
 * states *you have no models* — a claim about a machine we did not manage to ask.
 *
 * The URL is in the query key rather than read once, so editing the row above re-asks the new
 * server instead of answering about the old one.
 */
/** How often the inline suggestions are taken, on this machine.
 *
 *  `rate` is null until something has been accepted or dismissed, and the row says so in words
 *  rather than showing 0%. Zero would be a claim — "nobody wants these" — where the truth is that
 *  nobody has answered yet, and that distinction is the whole reason the field is nullable.
 */
function CompletionAcceptanceRow() {
  const t = useT();
  const stats = useQuery({ queryKey: ["completion-stats"], queryFn: getCompletionStats });
  const data = stats.data;
  const shown =
    !data || data.rate === null || data.rate === undefined
      ? t("settings.value.acceptanceNone")
      : t("settings.value.acceptance", {
          accepted: data.accepted,
          dismissed: data.dismissed,
          rate: `${Math.round(data.rate * 100)}%`,
        });
  return (
    <Row label={t("settings.row.acceptance")}>
      <span className="text-xs text-muted-foreground">{shown}</span>
    </Row>
  );
}


/** How often the next-step suggestions under a Code-screen answer are taken (study 29, P4.5): picked
 *  out of shown, and sent out of picked. The same rule as the row above — no rate is drawn until one
 *  has been shown, because "0 of 0" read as a verdict would be one nobody gave. */
function SuggestionAcceptanceRow() {
  const t = useT();
  // An arrow, not the bare reference: a screen that cannot read the rate must still open.
  const stats = useQuery({ queryKey: ["suggestion-stats"], queryFn: () => getSuggestionStats() });
  const data = stats.data;
  const shown = !data || !data.shown
    ? t("settings.value.suggestionAcceptanceNone")
    : t("settings.value.suggestionAcceptance", { picked: data.picked, shown: data.shown, sent: data.sent });
  return (
    <Row label={t("settings.row.suggestionAcceptance")}>
      <span className="text-xs text-muted-foreground">{shown}</span>
    </Row>
  );
}

/**
 * What a command the agent runs can reach on the network HERE, and the one switch that changes it.
 *
 * `CHIMERA_SANDBOX_NETWORK` means something only inside a container that answered. Everywhere else
 * a "none / bridge" row would be a fence that does not exist: on Windows the default `auto` resolves
 * to this machine, whose network nothing fences, and a kernel sandbox (bubblewrap, Seatbelt) has no
 * network to give whatever the setting says. So the switch appears only when the sandbox that would
 * actually run a command is docker, and every other case says in words what is true instead.
 *
 * The answer comes from `GET /api/governance/sandbox`, which asks the sandbox object rather than
 * reading the setting: the same probe the Security screen and the posture line use, so the three
 * cannot disagree. `queryFn` is an arrow on purpose: the reference is resolved when the query runs,
 * so a caller with no such endpoint degrades to these rows saying nothing, never to a crash of the
 * screen. Silent on an error from our OWN endpoint, as the Ollama picker is: it is not evidence
 * about the machine.
 *
 * The Python row does not wait for that answer, because only one case needs it: a configured
 * `docker` that may or may not have answered. Every other mode runs `execute_code` on this
 * machine, which is what `doctor` already measured.
 */
function SandboxReachRows({
  mode,
  network,
  image,
  verifyNetwork,
  applies,
  python,
  save,
}: {
  /** The configured sandbox (`CHIMERA_SANDBOX`). Only `docker` can put a command in a container. */
  mode: string;
  network: string;
  image: string;
  /** `CHIMERA_VERIFY_NETWORK`: the verifier's own exception to whatever these rows say. */
  verifyNetwork: boolean;
  applies?: string;
  python?: DoctorInfo["code_python"];
  save: (updates: Record<string, string>) => void;
}) {
  const t = useT();
  // The Security screen's rule, and it has to be the same here: probed on every open, never kept.
  // TanStack keeps the LONGEST gcTime any observer of a key asked for, so this observer with the app
  // defaults (fresh 30 s, kept 5 min) would hand Security a dead daemon's "isolated" while it
  // re-probed — and this card would offer the container switch on a cached answer.
  const state = useQuery({
    queryKey: ["governance-sandbox"],
    queryFn: () => getSandboxState(),
    staleTime: 0,
    gcTime: 0,
  });
  const s: SandboxState | undefined = state.data;
  if (!s) {
    // Not known yet, or our endpoint failed. Outside `docker` that changes nothing about the Python;
    // with `docker`, whether a container answers is exactly what is not known, so say nothing.
    return mode === "docker" ? null : (
      <CodePythonRow python={python} inContainer={false} image={image} />
    );
  }
  const inContainer = s.backend === "docker";
  return (
    <>
      {inContainer ? (
        <Row
          label={t("settings.row.sandboxNetwork")}
          hint={
            network === "bridge"
              ? t("settings.hint.sandboxNetworkBridge")
              : // Closed for shell commands and execute_code; the verifier may still open it.
                withException(
                  t("settings.hint.sandboxNetworkNone"),
                  verifyNetwork && t("settings.hint.verifyNetworkContainer"),
                )
          }
          // Bridge is the value that widens what a command can reach, so its hint is the warning.
          warn={network === "bridge"}
          applies={applies}
          env="CHIMERA_SANDBOX_NETWORK"
        >
          <Select
            value={network === "bridge" ? "bridge" : "none"}
            options={["none", "bridge"]}
            render={(v) =>
              t(v === "bridge" ? "settings.value.networkBridge" : "settings.value.networkNone")
            }
            onChange={(v) => save({ CHIMERA_SANDBOX_NETWORK: v })}
          />
        </Row>
      ) : (
        <NetworkFactRow state={s} verifyNetwork={verifyNetwork} />
      )}
      <CodePythonRow python={python} inContainer={inContainer} image={image} />
    </>
  );
}

/** A row's sentence plus the exception that qualifies it, when there is one. */
function withException(base: string, exception: string | false): string {
  return exception ? `${base} ${exception}` : base;
}

/** The network outside a container that answered: closed by a kernel sandbox, or this machine's own. */
function NetworkFactRow({ state, verifyNetwork }: { state: SandboxState; verifyNetwork: boolean }) {
  const t = useT();
  // An older server sends no `network`. Derived the only safe way: isolated without a container is a
  // kernel sandbox, which has no network to give; anything else is the host, never "closed".
  const reach = state.network ?? (state.isolated ? "none" : "host");
  const blocked = reach === "none";
  const hint = blocked
    ? "settings.hint.networkOsBlocked"
    : state.reason_code === "no_container"
      ? "settings.hint.networkNoContainer"
      : "settings.hint.networkHost";
  return (
    <Row
      label={t("settings.row.sandboxNetwork")}
      // "Blocked" holds for shell commands and execute_code. The verifier, when it is asked for a
      // network the kernel sandbox cannot give, runs a command the user typed on this machine.
      hint={withException(t(hint), blocked && verifyNetwork && t("settings.hint.verifyNetworkHost"))}
      warn={!blocked}
    >
      <span className="text-xs text-muted-foreground">
        {t(blocked ? "settings.network.blocked" : "settings.network.host")}
      </span>
    </Row>
  );
}

/**
 * Which Python `execute_code` runs. The frozen desktop build has none of its own, so it is whatever
 * PATH holds, or nothing; and a snippet that could not start reads, in a transcript, exactly like a
 * model that wrote bad code. Inside a container the container's own interpreter answers instead.
 */
function CodePythonRow({
  python,
  inContainer,
  image,
}: {
  python?: DoctorInfo["code_python"];
  inContainer: boolean;
  image: string;
}) {
  const t = useT();
  const label = t("settings.row.codePython");
  if (inContainer) {
    // Not "python3": the command is `command -v python3 || command -v python`, so it is whichever the
    // image has — and nothing, for an image without one.
    return (
      <Row label={label} hint={t("settings.hint.codePythonImage", { image })}>
        <span className="text-xs text-muted-foreground">
          {t("settings.value.codePythonContainer")}
        </span>
      </Row>
    );
  }
  if (!python) return null;
  if (python.source === "missing" || !python.path) {
    return (
      <Row
        label={label}
        hint={t("settings.hint.codePythonMissing", {
          names: (python.looked_for ?? []).join(", "),
        })}
        warn
      >
        <span className="text-xs text-warn-foreground">
          {t("settings.value.codePythonMissing")}
        </span>
      </Row>
    );
  }
  return (
    <Row
      label={label}
      hint={t(
        python.source === "path"
          ? "settings.hint.codePythonPath"
          : "settings.hint.codePythonInterpreter",
      )}
    >
      <span
        className="max-w-56 truncate font-mono text-xs text-muted-foreground"
        title={python.path}
      >
        {python.path}
      </span>
    </Row>
  );
}

function OllamaModelPicker({
  baseUrl,
  current,
  onPick,
}: {
  baseUrl: string;
  /** The default model as configured, so a slug already pointing at Ollama shows as selected. */
  current: string;
  onPick: (slug: string) => void;
}) {
  const t = useT();
  // Read up here with the other hooks, not down at the `<select>`: every branch below returns early,
  // and a `useContext` after one of them is a hook that runs on some renders and not others.
  const rowLabel = useContext(RowLabelContext);
  const found = useQuery({
    queryKey: ["ollama-models", baseUrl],
    queryFn: getOllamaModels,
    // Never cached across time: a model pulled in a terminal thirty seconds ago is exactly the one
    // the user came here to select, and a stale list would send them back to typing it.
    staleTime: 0,
    gcTime: 0,
  });

  const data = found.data;
  // Silent while loading, and silent on an error from OUR OWN endpoint — which is not evidence about
  // the user's Ollama. Rendering "nothing answered at localhost:11434" because our sidecar hiccuped
  // would be the same fabrication this component exists to prevent, pointed at the wrong machine.
  if (!data) return null;
  if (!data.reachable) {
    // A missing reason on an unreachable answer is a server bug; saying nothing beats inventing one.
    if (!data.reason) return null;
    return (
      <span className="max-w-72 text-xs text-muted-foreground">
        {t(`settings.ollama.reason.${data.reason}`, { url: data.base_url })}
      </span>
    );
  }
  const models = data.models ?? [];
  if (models.length === 0) {
    return (
      <span className="max-w-72 text-xs text-muted-foreground">
        {t("settings.ollama.empty")}
      </span>
    );
  }

  // Selected only when the configured default IS one of these tags. A default pointing at a cloud
  // model must not be shown as a local one, and a stale `ollama_chat/…` slug for a tag that has
  // since been removed must show as unchosen rather than as a model that is there. So does a legacy
  // `ollama/…` slug: that prefix is Ollama's generate endpoint, which cannot call tools, and picking
  // the tag again is what migrates it.
  const selected = models.find((tag) => current === `ollama_chat/${tag}`) ?? "";
  return (
    <select
      className={inputCls}
      value={selected}
      aria-label={rowLabel}
      onChange={(e) => e.target.value && onPick(`ollama_chat/${e.target.value}`)}
    >
      <option value="">{t("settings.ollama.choose")}</option>
      {models.map((tag) => (
        <option key={tag} value={tag}>
          {tag}
        </option>
      ))}
    </select>
  );
}

function LanguageSelect() {
  const { lang, setLang } = useI18n();
  return (
    <select
      className={inputCls}
      // A bare select announced as "combo box, Português" — the value, with no statement of what
      // it selects. It sits in a Row like every other control here and simply never asked it.
      aria-label={useContext(RowLabelContext)}
      value={lang}
      onChange={(e) =>
        setLang(e.target.value as (typeof LANGS)[number]["code"])
      }
    >
      {LANGS.map((l) => (
        <option key={l.code} value={l.code}>
          {l.label}
        </option>
      ))}
    </select>
  );
}

function Select({
  value,
  options,
  onChange,
  render,
}: {
  value: string;
  options: string[];
  onChange: (v: string) => void;
  /**
   * How to WRITE an option. The value on the wire never changes — `read_only` is what the backend
   * validates and what `CHIMERA_REACH` holds — but a dropdown reading `workspace_shell` to someone
   * who chose Japanese is a raw enum, not a choice they were offered.
   *
   * Omitted on purpose where the value is a proper name: `json`, `sqlite`, `docker` are what those
   * things are called, and translating them would invent products that do not exist.
   */
  render?: (v: string) => string;
}) {
  // Named from its Row, the same way Toggle is: before this, every select on the screen — cost
  // mode, memory backend, sandbox, and now the three autonomy controls — announced as an unlabelled
  // combobox, which is the whole list of choices with no statement of what is being chosen.
  return (
    <select
      className={inputCls}
      value={value}
      aria-label={useContext(RowLabelContext)}
      onChange={(e) => onChange(e.target.value)}
    >
      {options.map((o) => (
        <option key={o} value={o}>
          {render ? render(o) : o}
        </option>
      ))}
    </select>
  );
}

function SecretField({
  provider,
  onSave,
}: {
  provider: ProviderCfg;
  onSave: (v: string) => void;
}) {
  const t = useT();
  const rowLabel = useContext(RowLabelContext);
  const [editing, setEditing] = useState(false);
  const [v, setV] = useState("");
  if (!editing) {
    return (
      <>
        {provider.set ? (
          <span className="flex items-center gap-1 text-xs text-muted-foreground">
            <Check className="h-3.5 w-3.5 text-ok" /> {t("settings.isSet")}{" "}
            {provider.hint}
            {/* Where the key lives, when it is not the file (study 29, P7.7). A fact about the
                key's place, never about its value. */}
            {provider.in_vault ? (
              <span className="ml-1 inline-flex items-center gap-1">
                <KeyRound className="h-3.5 w-3.5" aria-hidden />
                <span>{t("settings.vault.badge")}</span>
              </span>
            ) : null}
          </span>
        ) : (
          <span className="text-xs text-muted-foreground">
            {t("settings.notSet")}
          </span>
        )}
        <Button size="sm" variant="outline" onClick={() => setEditing(true)}>
          {provider.set ? t("common.replace") : t("common.set")}
        </Button>
      </>
    );
  }
  return (
    <>
      <input
        className={inputCls}
        type="password"
        autoFocus
        placeholder={t("settings.pasteKey")}
        // Named from its Row, like the model field above. Five of these render at once on the
        // Conexões tab and every one announced the same thing — "cole a chave…", the placeholder,
        // with nothing saying WHICH provider. A placeholder is not a label: it disappears the
        // moment there is content, and on a password field where the wrong paste is a real error
        // that is the point at which knowing matters most.
        aria-label={rowLabel}
        value={v}
        onChange={(e) => setV(e.target.value)}
      />
      <Button
        size="sm"
        disabled={!v.trim()}
        onClick={() => {
          onSave(v.trim());
          setEditing(false);
          setV("");
        }}
      >
        {t("common.save")}
      </Button>
    </>
  );
}

/**
 * A provider's rotation pool: the keys it spreads load across and fails over between.
 *
 * Deliberately NOT a text field holding the comma-separated list, which is the shape the setting has
 * in `.env` and the obvious thing to render. A text field has to show its current value to be
 * editable; the value is secret, so it would show the mask; and the first person to click into it
 * and press Save writes `…abcd` over a working rotation. Here the existing keys are output only —
 * there is no control whose value is a key — and the two things you can do are add one and delete
 * one by position. The client never holds a key it did not just type.
 */
function PoolField({
  pool,
  onChanged,
}: {
  pool: PoolCfg;
  onChanged: () => void;
}) {
  const t = useT();
  const [v, setV] = useState("");
  const [error, setError] = useState("");
  // The server's own reason, under the generic line. "Check it is a key, not a masked hint" is the
  // common case, but not the only one: with the vault switch on, a pool can outgrow what Windows'
  // Credential Manager keeps per entry, and the server says so — a reason worth showing over a guess.
  const [reason, setReason] = useState("");
  // With the vault switch on and no vault on this machine, the server writes the whole pool to
  // `.env` in plain text and says so in `vault_fallback`. The switch's own row only reads the answer
  // of a PATCH, never of a pool write, so without this the pool would land in the file in silence.
  const [fellBack, setFellBack] = useState<readonly string[]>([]);
  const add = useMutation({
    mutationFn: () => addPoolKey(pool.provider, v.trim()),
    onSuccess: (data) => {
      setV("");
      setError("");
      setReason("");
      setFellBack(data.vault_fallback ?? []);
      onChanged();
    },
    onError: (e: unknown) => {
      setError(t("settings.pool.rejected"));
      setReason(e instanceof Error ? e.message : "");
    },
  });
  const drop = useMutation({
    mutationFn: (index: number) => removePoolKey(pool.provider, index),
    onSuccess: (data) => {
      setFellBack(data.vault_fallback ?? []);
      onChanged();
    },
  });

  return (
    <div className="space-y-2">
      {pool.keys.length === 0 ? (
        <p className="text-xs text-muted-foreground">
          {t("settings.pool.empty")}
        </p>
      ) : (
        <ul className="space-y-1">
          {pool.keys.map((k) => (
            <li
              key={k.index}
              className="flex items-center gap-2 text-xs text-muted-foreground"
            >
              <Check className="h-3.5 w-3.5 shrink-0 text-ok" />
              <span className="font-mono">{k.hint}</span>
              <button
                className="ml-auto text-muted-foreground hover:text-bad"
                aria-label={t("settings.pool.remove", { hint: k.hint })}
                disabled={drop.isPending}
                onClick={() => drop.mutate(k.index)}
              >
                <Trash2 className="h-3.5 w-3.5" />
              </button>
            </li>
          ))}
        </ul>
      )}
      <div className="flex items-center gap-2">
        <input
          className={inputCls}
          type="password"
          placeholder={t("settings.pasteKey")}
          // Third copy of this markup and the third to need naming. This one is not inside a Row,
          // so it names itself from the pool's provider — which is the whole distinction between
          // one pool's field and the next.
          aria-label={`${t("settings.pasteKey")} — ${pool.provider}`}
          value={v}
          onChange={(e) => {
            setV(e.target.value);
            setError("");
            setReason("");
          }}
        />
        <Button
          size="sm"
          disabled={!v.trim() || add.isPending}
          onClick={() => add.mutate()}
        >
          {t("settings.pool.add")}
        </Button>
      </div>
      {error ? <p className="text-xs text-bad-foreground">{error}</p> : null}
      {error && reason ? <p className="text-xs text-muted-foreground">{reason}</p> : null}
      {fellBack.length ? (
        <p className="text-xs text-warn-foreground">
          {t("settings.vault.fellBack", { keys: fellBack.join(", ") })}
        </p>
      ) : null}
    </div>
  );
}

/**
 * One messaging platform.
 *
 * Was hardcoded to Discord — the platform name appeared in four places and the status was read as
 * `status.data?.discord`. The backend has always taken a platform parameter and the manager has
 * always supported Telegram; the UI simply never offered it, so a configured Telegram token was
 * unreachable from the app.
 */
export function MessagingCard({
  save,
  platform = "discord",
  tokenEnv = "CHIMERA_DISCORD_BOT_TOKEN",
  allowed = [],
  allowedApplies,
  attach = false,
  attachApplies,
}: {
  save: (u: Record<string, string>) => void;
  platform?: "discord" | "telegram";
  tokenEnv?: string;
  /** The ids allowed to talk to this bot, as saved (`config.messaging.allowed_users`). Empty = anyone. */
  allowed?: readonly string[];
  /** When a saved list starts applying — the server's answer (`config.applies`), never a guess here. */
  allowedApplies?: string;
  /** Discord only: `CHIMERA_DISCORD_ATTACH_FILES` as saved (`config.messaging.discord_attach_files`). */
  attach?: boolean;
  attachApplies?: string;
}) {
  const t = useT();
  const qc = useQueryClient();
  const status = useQuery({ queryKey: ["messaging"], queryFn: getMessaging });
  const [editing, setEditing] = useState(false);
  const [token, setToken] = useState("");
  const invalidate = () => qc.invalidateQueries({ queryKey: ["messaging"] });
  const toggle = useMutation({
    mutationFn: (on: boolean) =>
      on ? startMessaging(platform) : stopMessaging(platform),
    onSuccess: invalidate,
  });

  const d = status.data?.[platform];
  const running = !!d?.running;
  const configured = !!d?.configured;
  const label = platform === "discord" ? "Discord" : "Telegram";
  // Derived from the platform rather than passed, so the field can never write one platform's list
  // into the other's variable.
  const allowedEnv =
    platform === "discord"
      ? "CHIMERA_DISCORD_ALLOWED_USERS"
      : "CHIMERA_TELEGRAM_ALLOWED_USERS";

  return (
    <Card title={`${t("settings.card.messaging")} · ${label}`}>
      <Row
        label={t("settings.row.botToken", { platform: label })}
        hint={t("settings.hint.botToken", { platform: label })}
        env={tokenEnv}
      >
        {editing ? (
          <>
            <input
              className={inputCls}
              type="password"
              autoFocus
              placeholder={t("settings.pasteKey")}
              // Named for its platform: Discord's token field and Telegram's are the same markup on
              // the same screen, and the placeholder cannot tell them apart.
              aria-label={t("settings.row.botToken", { platform: label })}
              value={token}
              onChange={(e) => setToken(e.target.value)}
            />
            <Button
              size="sm"
              disabled={!token.trim()}
              onClick={() => {
                save({ [tokenEnv]: token.trim() });
                setEditing(false);
                setToken("");
                setTimeout(invalidate, 300); // refresh "configured" after the save lands
              }}
            >
              {t("common.save")}
            </Button>
          </>
        ) : (
          <>
            {configured ? (
              <span className="flex items-center gap-1 text-xs text-muted-foreground">
                <Check className="h-3.5 w-3.5 text-ok" /> {t("settings.isSet")}
              </span>
            ) : (
              <span className="text-xs text-muted-foreground">
                {t("settings.notSet")}
              </span>
            )}
            <Button
              size="sm"
              variant="outline"
              onClick={() => setEditing(true)}
            >
              {configured ? t("common.replace") : t("common.set")}
            </Button>
          </>
        )}
      </Row>
      <Row
        label={t("settings.row.botRun", { platform: label })}
        hint={t("settings.hint.botRun")}
        env="CHIMERA_APP_MESSAGING"
      >
        <div className="flex items-center gap-2">
          {d?.error && !running && (
            <span
              className="max-w-[16rem] truncate text-xs text-bad-foreground"
              title={d.error}
            >
              {d.error}
            </span>
          )}
          <Toggle
            on={running}
            onChange={(v) => {
              save({ CHIMERA_APP_MESSAGING: String(v) }); // persist for boot auto-start
              toggle.mutate(v); // start/stop now
            }}
          />
        </div>
      </Row>
      {/* Who may talk to it. The adapters always took this list and nothing ever filled it, so a
          configured bot answered whoever could reach it — with the owner's tools and spend. An empty
          list still means anyone (the owner's call: refusing everyone would silence a bot already
          in use), but it is said here in words instead of being a blank field. */}
      <Row
        label={t("settings.row.botAllowed")}
        hint={t("settings.hint.botAllowed", { platform: label })}
        applies={allowedApplies}
        env={allowedEnv}
      >
        <TextField
          value={allowed.join(", ")}
          placeholder={t("settings.botAllowed.placeholder")}
          onSave={(v) => save({ [allowedEnv]: v })}
        />
      </Row>
      {/* A new bot the app started waits for its owner to send it this code (plan M7); the console
          that also logs it is read by nobody. While it runs, whether it is open is the server's
          answer: a bot paired with its owner has an empty list here and answers one person. */}
      {d?.pairing_code ? (
        <div className="px-4 pb-3 text-xs text-warn-foreground">
          {t("settings.messaging.pairing", { code: d.pairing_code })}
        </div>
      ) : configured &&
        (running && typeof d?.open === "boolean" ? d.open : allowed.length === 0) ? (
        <div className="px-4 pb-3 text-xs text-warn-foreground">
          {t("settings.messaging.open", { platform: label })}
        </div>
      ) : null}
      {/* Discord only: it is the one adapter that sends files. Below the allowlist because it depends
          on it — the server attaches nothing while the list is empty, and the line under the switch
          says so, so an "on" switch never reads as files going out when none do. */}
      {platform === "discord" ? (
        <>
          <Row
            label={t("settings.row.discordAttach")}
            hint={t("settings.hint.discordAttach")}
            applies={attachApplies}
            env="CHIMERA_DISCORD_ATTACH_FILES"
          >
            <Toggle
              on={attach}
              onChange={(v) => save({ CHIMERA_DISCORD_ATTACH_FILES: String(v) })}
            />
          </Row>
          {attach && allowed.length === 0 ? (
            <div className="px-4 pb-3 text-xs text-warn-foreground">
              {t("settings.messaging.attachRefused")}
            </div>
          ) : null}
        </>
      ) : null}
      {!configured && (
        <div className="px-4 pb-3 text-xs text-muted-foreground">
          {t("settings.messaging.note")}
        </div>
      )}
    </Card>
  );
}


export type SettingsTab =
  | "general"
  | "appearance"
  | "skills"
  | "connectors"
  | "plugins"
  | "connections"
  | "usage"
  | "security"
  | "decisions";

/** In the order the column shows them. */
export const SETTINGS_TABS: readonly SettingsTab[] = [
  "general",
  "appearance",
  "skills",
  "connectors",
  "plugins",
  "connections",
  "usage",
  "security",
  "decisions",
];

/**
 * Names a person may type for a tab that has another address. MCP servers and OpenAPI connectors
 * were sub-tabs of Connections kept in `useState`, so no link to them ever existed; both now live
 * under Connectors, and these are a courtesy for an address written by hand (study 04, §1.3).
 */
const SETTINGS_TAB_ALIASES: ReadonlyMap<string, SettingsTab> = new Map<string, SettingsTab>([
  ["mcp", "connectors"],
  ["openapi", "connectors"],
]);

/** A `?tab=` value from the URL, or General for anything that is not a tab — a stale link lands on
 *  the screen rather than on nothing. */
export function settingsTab(value: string | null | undefined): SettingsTab {
  const known = SETTINGS_TABS.find((tab) => tab === value);
  if (known) return known;
  return (value ? SETTINGS_TAB_ALIASES.get(value) : undefined) ?? "general";
}

/**
 * The tab can be owned by the caller. It lived only in `useState`, so nothing outside this screen
 * could point at a tab: the status bar's cost chip said it opened Usage and called
 * `navigate("settings")`, which always landed on General. `App` now keeps the tab in the route
 * (`#/settings?tab=usage`) and passes it down; rendered without `onTabChange` — the tests, an
 * embedded copy — the screen keeps its own state exactly as before.
 */
export function Settings({
  tab: routeTab,
  onTabChange,
}: { tab?: SettingsTab; onTabChange?: (tab: SettingsTab) => void } = {}) {
  const t = useT();
  const tabsId = useId();
  const [ownTab, setOwnTab] = useState<SettingsTab>("general");
  const [search, setSearch] = useState("");

  const searchRef = useRef<HTMLInputElement>(null);
  useEffect(() => {
    const shortcut = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        searchRef.current?.focus();
      }
    };
    window.addEventListener("keydown", shortcut);
    return () => window.removeEventListener("keydown", shortcut);
  }, []);
  const tab = onTabChange ? (routeTab ?? "general") : ownTab;
  const setTab = onTabChange ?? setOwnTab;
  const qc = useQueryClient();
  const config = useQuery({ queryKey: ["config"], queryFn: getConfig });
  const doctor = useQuery({ queryKey: ["doctor"], queryFn: getDoctor });
  const mutation = useMutation({
    mutationFn: patchConfig,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["config"] });
      qc.invalidateQueries({ queryKey: ["doctor"] });
      // The Storage card says where the next worktree goes, which a saved folder changes.
      qc.invalidateQueries({ queryKey: ["storage"] });
      // A changed sandbox changes what a command can reach; the network rows read it from there.
      qc.invalidateQueries({ queryKey: ["governance-sandbox"] });
    },
  });
  const save = (updates: Record<string, string>) => mutation.mutate(updates);

  if (config.isError) {
    return (
      <div className="flex flex-1 items-center justify-center">
        <ErrorState error={config.error} onRetry={() => config.refetch()} />
      </div>
    );
  }
  if (config.isLoading || !config.data) {
    return (
      <div className="flex flex-1 items-center justify-center text-muted-foreground">
        <Loader2 className="h-5 w-5 animate-spin" />
      </div>
    );
  }
  const c: AppConfig = config.data;
  const d: DoctorInfo | undefined = doctor.data;

  // Two titled groups below the two ungrouped sections (study 04, §1.1). Personalization is what
  // the owner ADDS — skills, connectors, plugins, each installed, switched on, updated and removed
  // the same way. System is what is set once: which server, what it may reach, what it costs.
  const personalize = t("settings.group.personalize");
  const system = t("settings.group.system");
  const tabs: TabItem<SettingsTab>[] = [
    { value: "general", label: t("settings.tab.general") },
    // Its own section: twelve rows about how the app looks were the first card of General, above
    // every row about what the agent may do.
    { value: "appearance", label: t("settings.card.appearance") },
    { value: "skills", label: t("settings.tab.skills"), group: personalize },
    { value: "connectors", label: t("settings.tab.connectors"), group: personalize },
    // As Claude keeps it under Customize: what a plugin brings is skills, commands, agents and MCP
    // servers — the things the two tabs above list one by one (plan B6).
    { value: "plugins", label: t("settings.tab.plugins"), group: personalize },
    { value: "connections", label: t("settings.tab.connections"), group: system },
    { value: "usage", label: t("nav.usage"), group: system },
    { value: "security", label: t("settings.tab.security"), group: system },
    { value: "decisions", label: t("settings.tab.decisions"), group: system },
  ];

  return (
    // Built here and read by every Row, rather than threaded through eleven cards as a prop: the
    // rows that need it are three components deep and none of them is otherwise interested in the
    // config object.
    <PinnedContext.Provider value={new Set(c.pinned ?? [])}>
      <div className="flex min-h-0 flex-1 flex-col">
        <div className="flex items-center gap-3 px-6 pt-6">
          <KeyRound className="h-5 w-5 text-accent" />
          <h1 className="text-lg font-semibold">{t("settings.title")}</h1>
          <div className="relative ml-auto flex min-w-48 max-w-md flex-1 items-center">
            <Search className="absolute left-2.5 h-4 w-4 text-muted-foreground" aria-hidden="true" />
            <input
              ref={searchRef}
              className="field h-9 w-full pl-8 pr-16 text-sm"
              aria-label={t("settings.search.label")}
              placeholder={t("settings.search.placeholder")}
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Escape") setSearch("");
              }}
            />
            <kbd className="pointer-events-none absolute right-2 text-xs text-muted-foreground">Ctrl K</kbd>
          </div>
          {mutation.isPending && (
            <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
          )}
        </div>
        {/* Sections in a column beside the content from `lg` up, the strip below it. Six sections
            and a long General read as a document with a table of contents, not as a menu bar. */}
        <div className="flex min-h-0 flex-1 flex-col lg:flex-row">
        <Tabs
          items={tabs}
          value={tab}
          onChange={setTab}
          aria-label={t("settings.title")}
          orientation="vertical"
          className="px-6 lg:w-52 lg:shrink-0 lg:px-3 lg:pt-4"
        />
        <div className="min-h-0 flex-1 overflow-y-auto" data-settings-scroll>
          {search.trim() ? (
            <SettingsSearchResults
              query={search}
              tabLabel={(next) => tabs.find((item) => item.value === next)?.label ?? ""}
              onOpen={(next) => {
                setTab(next);
                setSearch("");
              }}
            />
          ) : <TabPanel tabsId={tabsId} value={tab}>
            {/* Skills on the shared catalog shell (study 04, PR 5); Connectors still embeds its screens. */}
            {tab === "skills" && (
              <div className="mx-auto max-w-3xl px-6 py-6">
                <Skills embedded />
              </div>
            )}
            {tab === "connectors" && <Connectors />}
            {tab === "connections" && <Connections />}
            {tab === "plugins" && <Plugins />}
            {tab === "usage" && <Usage embedded />}
            {tab === "security" && (
              <Governance embedded onOpenSettings={() => setTab("general")} />
            )}
            {tab === "decisions" && <Decisions embedded />}
            {tab === "appearance" && (
              <div className="max-w-5xl space-y-6 px-6 py-6">
                <Card title={t("settings.card.appearance")}>
                  <Row
                    label={t("settings.row.language")}
                    hint={t("settings.hint.language")}
                  >
                    <LanguageSelect />
                  </Row>
                  {/* Display only, and every default is the page as it was before these rows:
                      the OS theme and motion, medium text, the medium conversation width and the
                      system fonts. Kept by this machine (lib/theme.ts) except the width, which is
                      part of the screen's layout. */}
                  <Row label={t("settings.row.theme")} hint={t("settings.hint.theme")}>
                    <ThemeSelect name={t("settings.row.theme")} />
                  </Row>
                  {/* Your own theme, on top of light or dark. Each default is the shipped theme. */}
                  <Row label={t("settings.row.accent")} hint={t("settings.hint.accent")}>
                    <AccentPicker name={t("settings.row.accent")} />
                  </Row>
                  <Row label={t("settings.row.tint")} hint={t("settings.hint.tint")}>
                    <TintSelect name={t("settings.row.tint")} />
                  </Row>
                  <Row label={t("settings.row.contrast")} hint={t("settings.hint.contrast")}>
                    <ContrastSelect name={t("settings.row.contrast")} />
                  </Row>
                  <Row label={t("settings.row.motion")} hint={t("settings.hint.motion")}>
                    <MotionSelect name={t("settings.row.motion")} />
                  </Row>
                  <Row label={t("settings.row.textSize")} hint={t("settings.hint.textSize")}>
                    <TextSizeSelect name={t("settings.row.textSize")} />
                  </Row>
                  <Row
                    label={t("settings.row.transcriptWidth")}
                    hint={t("settings.hint.transcriptWidth")}
                  >
                    <TranscriptWidthSelect name={t("settings.row.transcriptWidth")} />
                  </Row>
                  <Row label={t("settings.row.transcriptView")} hint={t("settings.hint.transcriptView")}>
                    <TranscriptViewSelect name={t("settings.row.transcriptView")} />
                  </Row>
                  <Row label={t("settings.row.uiZoom")} hint={t("settings.hint.uiZoom")}>
                    <UiZoomSelect name={t("settings.row.uiZoom")} />
                  </Row>
                  <Row label={t("settings.row.uiFont")} hint={t("settings.hint.uiFont")}>
                    <UiFontSelect name={t("settings.row.uiFont")} />
                  </Row>
                  <Row label={t("settings.row.codeFont")} hint={t("settings.hint.codeFont")}>
                    <CodeFontSelect name={t("settings.row.codeFont")} />
                  </Row>
                </Card>
              </div>
            )}
            {tab === "general" && (
              <div className="w-full divide-y divide-hairline">
                <section className="max-w-5xl space-y-6 px-6 py-6">
                <Card title={t("settings.card.basics")}>
                  <Row label={t("settings.row.language")} hint={t("settings.hint.language")}>
                    <LanguageSelect />
                  </Row>
                  <Row label={t("code.sessions.defaultProject")} hint={t("code.workspacePlaceholder")}>
                    <TextField
                      value={readWorkspace()}
                      placeholder={t("code.workspacePlaceholder")}
                      onSave={writeWorkspace}
                    />
                  </Row>
                  <Row label={t("settings.row.defaultModel")} env="CHIMERA_DEFAULT_MODEL">
                    <TextField
                      value={c.models.default}
                      placeholder="openrouter/…"
                      onSave={(v) => save({ CHIMERA_DEFAULT_MODEL: v })}
                    />
                  </Row>
                  <ResponseStyleNote />
                </Card>

                <IdentityCard />

                {d && (
                  <Card title={t("settings.card.status")}>
                    <Row label={t("settings.row.providersWithKey")}>
                      <span className="text-sm">
                        {d.configured_providers.length
                          ? d.configured_providers.join(", ")
                          : t("settings.none")}
                      </span>
                    </Row>
                    <Row
                      label={t("settings.row.modelLadder")}
                      hint={t("settings.hint.modelLadder")}
                    >
                      <span className="max-w-56 truncate font-mono text-xs">
                        {d.tiers.weak} · {d.tiers.mid} · {d.tiers.top}
                      </span>
                    </Row>
                  </Card>
                )}
                </section>
                <section className="max-w-5xl space-y-6 px-6 py-6" aria-label={t("settings.card.voice")}>
                <VoiceCard>
                  {/* This model handles the spoken reply; detailed benchmark context is available
                      on demand in the row below. */}
                  <Row
                    label={t("settings.row.voiceModel")}
                    hint={t("settings.hint.voiceModelShort")}
                    env="CHIMERA_VOICE_MODEL"
                  >
                    <ModelPicker
                      value={c.models.voice_model}
                      onChange={(slug) => save({ CHIMERA_VOICE_MODEL: slug })}
                      label={null}
                      name={t("settings.row.voiceModel")}
                      fallback={t("settings.placeholder.sameModel")}
                      offerDefault={false}
                      quickFirst
                      blurb={t("settings.voice.pickBlurb")}
                    />
                  </Row>
                  <details className="border-t border-hairline px-4 py-2 text-xs text-muted-foreground">
                    <summary className="cursor-pointer">{t("settings.action.learnMore")}</summary>
                    <p className="pt-2">{t("settings.hint.voiceModel")}</p>
                  </details>
                  {/* The other half of hands-free: the model that does what the voice asked for
                      — with its thinking, because "fix the login" is answered by the fix. Empty is
                      the conversation's model, which is what every install has. */}
                  <Row
                    label={t("settings.row.voiceWorkModel")}
                    hint={t("settings.hint.voiceWorkModel")}
                    env="CHIMERA_VOICE_WORK_MODEL"
                  >
                    <ModelPicker
                      value={c.models.voice_work_model}
                      onChange={(slug) => save({ CHIMERA_VOICE_WORK_MODEL: slug })}
                      label={null}
                      name={t("settings.row.voiceWorkModel")}
                      fallback={t("settings.placeholder.sameModel")}
                      offerDefault={false}
                      blurb={t("settings.voice.pickBlurb")}
                    />
                  </Row>
                  </VoiceCard>
                </section>

                <section className="max-w-5xl space-y-6 px-6 py-6">
                <NotificationsCard />

                {/* The tray's own switches (study 29, P2.2/P2.4/P2.6), which only its menu could
                    change: written to the file the desktop shell reads, through the backend. */}
                <ShellCard />

                {/* Archiving idle conversations, which shipped with no row. */}
                <ConversationsCard
                  archiveAfterDays={c.conversations?.archive_after_days ?? null}
                />

                <AutonomyCard c={c} save={save} />

                {/* Beside the card that holds the governance switch: the opt-in rules and the
                    audit record that sit on top of it. */}
                <GovernanceAuditCard c={c} save={save} />

                {/* Where commands may run, folder by folder — the server's record (study 29, P4.3). */}
                <FoldersCard reach={c.autonomy.reach} />

                <Card title={t("settings.card.model")}>
                  <Row
                    label={t("settings.row.costMode")}
                    hint={t("settings.hint.costMode")}
                    env="CHIMERA_COST_MODE"
                  >
                    <Select
                      value={c.models.cost_mode}
                      options={["auto", "cheap", "balanced", "premium"]}
                      render={(v) => t(`settings.value.${v}`)}
                      onChange={(v) => save({ CHIMERA_COST_MODE: v })}
                    />
                  </Row>
                  {/* The three rungs. The Status card above SHOWS the resolved ladder and, until now, there
              was no way to pin any of it: someone who wanted cheap-on-easy and strong-on-hard could
              read what they were getting and not change it. Empty means "let the cost mode decide",
              which is what every install has. */}
                  <Row
                    label={t("settings.row.weakModel")}
                    hint={t("settings.hint.roleModels")}
                    env="CHIMERA_WEAK_MODEL"
                  >
                    <TextField
                      value={c.models.weak}
                      placeholder={t("settings.placeholder.byCostMode")}
                      onSave={(v) => save({ CHIMERA_WEAK_MODEL: v })}
                    />
                  </Row>
                  <Row
                    label={t("settings.row.midModel")}
                    env="CHIMERA_MID_MODEL"
                  >
                    <TextField
                      value={c.models.mid}
                      placeholder={t("settings.placeholder.byCostMode")}
                      onSave={(v) => save({ CHIMERA_MID_MODEL: v })}
                    />
                  </Row>
                  <Row
                    label={t("settings.row.orchestratorModel")}
                    env="CHIMERA_ORCHESTRATOR_MODEL"
                  >
                    <TextField
                      value={c.models.orchestrator}
                      placeholder={t("settings.placeholder.byCostMode")}
                      onSave={(v) => save({ CHIMERA_ORCHESTRATOR_MODEL: v })}
                    />
                  </Row>
                  {/* What makes a fully local install reachable from the interface rather than from a file.
              Ollama and vLLM both speak the OpenAI protocol, so this one field is the difference
              between "supports local models" and "supports local models if you edit .env". */}
                  <Row
                    label={t("settings.row.apiBase")}
                    hint={t("settings.hint.apiBase")}
                    env="CHIMERA_API_BASE"
                  >
                    <TextField
                      value={c.models.api_base ?? ""}
                      placeholder="http://localhost:11434/v1"
                      onSave={(v) => save({ CHIMERA_API_BASE: v })}
                    />
                  </Row>
                  {/* Not the same field as the one above, and the difference matters: `api_base` is sent on
              EVERY call, so pointing it at Ollama redirects the cloud providers too. This one only
              tells LiteLLM's Ollama provider where the server is, which is what someone running
              Ollama on another machine actually wants. */}
                  <Row
                    label={t("settings.row.ollamaUrl")}
                    hint={t("settings.hint.ollamaUrl")}
                    env="CHIMERA_OLLAMA_BASE_URL"
                  >
                    <TextField
                      value={c.models.ollama_base_url}
                      placeholder="http://localhost:11434"
                      onSave={(v) => save({ CHIMERA_OLLAMA_BASE_URL: v })}
                    />
                  </Row>
                  {/* Directly under the URL it asks, because the answer is only as good as that field — and
              because every model box on this screen was a memory test until this row existed. It
              writes the default model rather than a fourth model field of its own: "point this at a
              model I actually have" is the whole reason someone came here. */}
                  <Row
                    label={t("settings.row.ollamaModels")}
                    hint={t("settings.hint.ollamaModels")}
                  >
                    <OllamaModelPicker
                      baseUrl={c.models.ollama_base_url}
                      current={c.models.default}
                      onPick={(slug) => save({ CHIMERA_DEFAULT_MODEL: slug })}
                    />
                  </Row>
                  {/* Directly under the Ollama URL, because it is useless without it and the pair is one
              decision. The hint carries the part nobody guesses: it must be a BASE tag. */}
                  <Row
                    label={t("settings.row.completeModel")}
                    hint={t("settings.hint.completeModel")}
                    env="CHIMERA_COMPLETE_MODEL"
                  >
                    <TextField
                      value={c.models.complete_model}
                      placeholder="qwen2.5-coder:1.5b-base"
                      onSave={(v) => save({ CHIMERA_COMPLETE_MODEL: v })}
                    />
                  </Row>
                  {/* What the machine already knows and never told anyone.
                      Every accept and dismiss is posted to the server by the editor
                      (`postCompletionOutcome`), and `getCompletionStats` — the endpoint that reads
                      it back — had no caller in the app at all. So the product collected an
                      answer to "are these suggestions any good" and showed it to nobody. */}
                  <CompletionAcceptanceRow />
                  <SuggestionAcceptanceRow />
                  <Row
                    label={t("settings.row.fallbackModels")}
                    hint={t("settings.hint.fallbackModels")}
                    env="CHIMERA_FALLBACK_MODELS"
                  >
                    <TextField
                      value={c.models.fallback_models.join(", ")}
                      placeholder="openrouter/…, openrouter/…"
                      onSave={(v) => save({ CHIMERA_FALLBACK_MODELS: v })}
                    />
                  </Row>
                  <Row
                    label={t("settings.row.cascade")}
                    hint={t("settings.hint.cascade")}
                    applies={c.applies?.CHIMERA_CASCADE}
                    env="CHIMERA_CASCADE"
                  >
                    <Toggle
                      on={c.models.cascade}
                      onChange={(v) => save({ CHIMERA_CASCADE: String(v) })}
                    />
                  </Row>
                  {/* What an OpenRouter route may keep. Both rows narrow which upstream providers may
                      answer, so both ship off and the hint says what turning them on costs; the
                      Security screen's privacy card reads the same two values back. A server that
                      predates the block reports nothing, which is the shipped default. */}
                  <Row
                    label={t("settings.row.orDataCollection")}
                    hint={t("settings.hint.orDataCollection")}
                    env="CHIMERA_OPENROUTER_DATA_COLLECTION"
                  >
                    <Select
                      value={c.privacy?.openrouter_data_collection === "deny" ? "deny" : "allow"}
                      options={["allow", "deny"]}
                      render={(v) =>
                        v === "deny"
                          ? t("settings.orDataCollection.deny")
                          : t("settings.orDataCollection.allow")
                      }
                      onChange={(v) => save({ CHIMERA_OPENROUTER_DATA_COLLECTION: v })}
                    />
                  </Row>
                  <Row
                    label={t("settings.row.orZdr")}
                    hint={t("settings.hint.orZdr")}
                    env="CHIMERA_OPENROUTER_ZDR"
                  >
                    <Toggle
                      on={c.privacy?.openrouter_zdr ?? false}
                      onChange={(v) => save({ CHIMERA_OPENROUTER_ZDR: String(v) })}
                    />
                  </Row>
                  {/* Whether the agent's read tools may read Chimera's own `.env` (owner's
                    decision, 2026-10-04). On by default, as it always was; the hint says what that
                    means in plain words, because the keys there would reach the model. */}
                  <Row
                    label={t("settings.row.agentReadsOwnEnv")}
                    // The second sentence is the bridge's: a run Claude starts never gets the file,
                    // whatever this row says (owner's decision of 2026-10-04).
                    hint={`${t("settings.hint.agentReadsOwnEnv")} ${t("settings.hint.agentReadsOwnEnvBridge")}`}
                    env="CHIMERA_AGENT_READS_OWN_ENV"
                  >
                    <Toggle
                      on={c.privacy?.agent_reads_own_env ?? true}
                      onChange={(v) => save({ CHIMERA_AGENT_READS_OWN_ENV: String(v) })}
                    />
                  </Row>
                </Card>

                <Card title={t("settings.card.apiKeys")}>
                  {/* Where the next key typed below is saved (study 29, P7.7). Off: `.env`, as
                    always. On: the OS vault, with a comment left in the file. With no vault on this
                    machine the hint says so before the switch is touched, and a save that fell back
                    names the keys it wrote to the file — a switch reading "on" over a plain-text
                    key is the one state this row must never show silently. */}
                  <Row
                    label={t("settings.row.keyVault")}
                    // The server token is the one key the switch never moves: the desktop shell
                    // reads it from `.env` for the tray and cannot read the vault
                    // (`api/key_vault.py`, SCREEN_STORABLE). Said here so the owner is not left to
                    // find a plain-text line the switch seemed to promise away.
                    hint={
                      c.vault?.available
                        ? `${t("settings.hint.keyVault")} ${t("settings.hint.keyVaultServerToken")}`
                        : t("settings.hint.keyVaultUnavailable")
                    }
                    warn={!c.vault?.available}
                    env="CHIMERA_KEY_VAULT"
                    note={
                      mutation.data?.vault_fallback?.length ? (
                        <div className="text-xs text-warn-foreground">
                          {t("settings.vault.fellBack", {
                            keys: mutation.data.vault_fallback.join(", "),
                          })}
                        </div>
                      ) : null
                    }
                  >
                    <Toggle
                      on={c.vault?.enabled ?? false}
                      onChange={(v) => save({ CHIMERA_KEY_VAULT: String(v) })}
                    />
                  </Row>
                  {c.vault?.available ? (
                    <Row
                      label={t("settings.row.keyVaultMove")}
                      hint={t("settings.hint.keyVaultMove")}
                    >
                      <KeyVaultMove
                        enabled={c.vault.enabled ?? false}
                        inVault={c.vault.keys?.length ?? 0}
                      />
                    </Row>
                  ) : null}
                  {c.providers.map((p) => (
                    <Row key={p.env} label={p.label} hint={p.env} env={p.env}>
                      <SecretField
                        provider={p}
                        onSave={(v) => save({ [p.env]: v })}
                      />
                    </Row>
                  ))}
                </Card>

                {/* Existed, worked, rotated round-robin with a cooldown per key — and no interface could
            reach it, so the whole feature was a paragraph in .env.example. */}
                {/* What most people never change, folded rather than removed: the rows are all still
                    here, one click away, and the summary says what is inside. */}
                <details className="group space-y-6">
                  <summary className="cursor-pointer list-none rounded-xl2 border border-hairline bg-card px-4 py-3">
                    <span className="text-sm font-semibold">{t("settings.advanced")}</span>
                    <span className="block text-xs text-muted-foreground">{t("settings.advancedHint")}</span>
                  </summary>
                  <div className="space-y-6 pt-6">
                <Card title={t("settings.card.pools")}>
                  <Row
                    label={t("settings.row.poolsIntro")}
                    hint={t("settings.hint.pools")}
                  >
                    <span />
                  </Row>
                  {(c.pools ?? []).map((p) => (
                    <Row
                      key={p.provider}
                      label={p.provider}
                      hint={p.env}
                      env={p.env}
                    >
                      <PoolField
                        pool={p}
                        onChanged={() =>
                          void qc.invalidateQueries({ queryKey: ["config"] })
                        }
                      />
                    </Row>
                  ))}
                </Card>

                <Card title={t("settings.card.memory")}>
                  <Row
                    label={t("settings.row.backend")}
                    env="CHIMERA_MEMORY_BACKEND"
                  >
                    <Select
                      value={c.memory.backend}
                      options={["json", "sqlite"]}
                      onChange={(v) => save({ CHIMERA_MEMORY_BACKEND: v })}
                    />
                  </Row>
                  <Row
                    label={t("settings.row.semantic")}
                    hint={t("settings.hint.semantic")}
                    env="CHIMERA_SEMANTIC_MEMORY"
                  >
                    <Toggle
                      on={c.memory.semantic}
                      onChange={(v) =>
                        save({ CHIMERA_SEMANTIC_MEMORY: String(v) })
                      }
                    />
                  </Row>
                  {/* Directly under the switch that depends on it. Semantic recall falls back to lexical on
              ANY embedder failure, silently — so an embed model the user's key cannot serve made the
              toggle above confirm a change it did not make. */}
                  <Row
                    label={t("settings.row.embedModel")}
                    hint={t("settings.hint.embedModel")}
                    env="CHIMERA_EMBED_MODEL"
                  >
                    <TextField
                      value={c.memory.embed_model}
                      placeholder="openrouter/openai/text-embedding-3-small"
                      onSave={(v) => save({ CHIMERA_EMBED_MODEL: v })}
                    />
                  </Row>
                  <Row
                    label={t("settings.row.autoConsolidate")}
                    hint={t("settings.hint.autoConsolidate")}
                    env="CHIMERA_AUTO_CONSOLIDATE"
                  >
                    <Toggle
                      on={c.memory.auto_consolidate}
                      onChange={(v) =>
                        save({ CHIMERA_AUTO_CONSOLIDATE: String(v) })
                      }
                    />
                  </Row>
                  {/* The one that closes the loop. Skills are extracted from successful runs either way;
              without this they are never read back, so the agent learns and does not use it. */}
                  <Row
                    label={t("settings.row.skillCards")}
                    hint={t("settings.hint.skillCards")}
                    env="CHIMERA_SKILL_CARDS"
                  >
                    <Toggle
                      on={c.memory.skill_cards}
                      onChange={(v) => save({ CHIMERA_SKILL_CARDS: String(v) })}
                    />
                  </Row>
                  <Row
                    label={t("settings.row.rememberChat")}
                    hint={t("settings.hint.rememberChat")}
                    applies={c.applies?.CHIMERA_CHAT_MEMORY}
                    env="CHIMERA_CHAT_MEMORY"
                  >
                    <Toggle
                      on={c.memory.remember_from_chat}
                      onChange={(v) => save({ CHIMERA_CHAT_MEMORY: String(v) })}
                    />
                  </Row>
                </Card>

                <MessagingCard
                  save={save}
                  allowed={c.messaging?.allowed_users?.discord ?? []}
                  allowedApplies={c.applies?.CHIMERA_DISCORD_ALLOWED_USERS}
                  attach={c.messaging?.discord_attach_files ?? false}
                  attachApplies={c.applies?.CHIMERA_DISCORD_ATTACH_FILES}
                />
                <MessagingCard
                  save={save}
                  platform="telegram"
                  tokenEnv="CHIMERA_TELEGRAM_BOT_TOKEN"
                  allowed={c.messaging?.allowed_users?.telegram ?? []}
                  allowedApplies={c.applies?.CHIMERA_TELEGRAM_ALLOWED_USERS}
                />

                <Card title={t("settings.card.cacheSandbox")}>
                  <Row
                    label={t("settings.row.completionCache")}
                    hint={t("settings.hint.completionCache")}
                    env="CHIMERA_CACHE"
                  >
                    <Toggle
                      on={c.cache.completion}
                      onChange={(v) => save({ CHIMERA_CACHE: String(v) })}
                    />
                  </Row>
                  <Row
                    label={t("settings.row.promptCache")}
                    hint={t("settings.hint.promptCache")}
                    env="CHIMERA_PROMPT_CACHE"
                  >
                    <Toggle
                      on={c.cache.prompt}
                      onChange={(v) =>
                        save({ CHIMERA_PROMPT_CACHE: String(v) })
                      }
                    />
                  </Row>
                  <Row label={t("settings.row.sandbox")} env="CHIMERA_SANDBOX">
                    {/* `auto` is the shipped default and was missing from the list, so the browser
              showed the first option ("local") for a sandbox that was not set to local, and `auto`
              could not be chosen back once left. The configured value is always offered, so an
              `os` set by hand reads as itself rather than as the first entry. */}
                    <Select
                      value={c.sandbox.mode}
                      options={Array.from(
                        new Set(["auto", "local", "docker", c.sandbox.mode]),
                      )}
                      render={(v) =>
                        v === "auto"
                          ? t("settings.value.sandboxAuto")
                          : v === "local"
                            ? t("settings.value.local")
                            : v
                      }
                      onChange={(v) => save({ CHIMERA_SANDBOX: v })}
                    />
                  </Row>
                  {/* Only meaningful under the docker sandbox, so it is shown only then — a field that
              changes nothing is a field someone will change and then wonder about. */}
                  {c.sandbox.mode === "docker" ? (
                    <Row
                      label={t("settings.row.sandboxImage")}
                      env="CHIMERA_SANDBOX_IMAGE"
                    >
                      <TextField
                        value={c.sandbox.image}
                        placeholder="python:3.12-slim"
                        onSave={(v) => save({ CHIMERA_SANDBOX_IMAGE: v })}
                      />
                    </Row>
                  ) : null}
                  <SandboxReachRows
                    mode={c.sandbox.mode}
                    network={c.sandbox.network ?? "none"}
                    image={c.sandbox.image}
                    verifyNetwork={c.sandbox.verify_network ?? false}
                    applies={c.applies?.CHIMERA_SANDBOX_NETWORK}
                    python={d?.code_python}
                    save={save}
                  />
                  {/* Filed with the sandbox rows because it answers the same question they do — where does
              the agent's work happen on this machine — for the one tool that has a window.

              The switch is inverted on purpose. The setting is `headless`, which is the state you
              cannot see; a control has to be named for what turning it ON does, and "Headless: off"
              asks the reader to hold a negation to find out whether they will see anything. */}
                  <Row
                    label={t("settings.row.showBrowser")}
                    hint={t("settings.hint.showBrowser")}
                    applies={c.applies?.CHIMERA_BROWSER_HEADLESS}
                    env="CHIMERA_BROWSER_HEADLESS"
                  >
                    <Toggle
                      on={!(c.browser?.headless ?? true)}
                      onChange={(v) =>
                        save({ CHIMERA_BROWSER_HEADLESS: String(!v) })
                      }
                    />
                  </Row>
                  {/* Where that browser may go (study 29, P5.2). Both empty by default, which is the
              browser as it always was. The values are shown as written, not masked: they are the
              owner's own statements, and a list that cannot be read back cannot be corrected. The
              server refuses a value it would not read (a URL in the site list, Chimera's own port
              in the ports), so a typo is an error here rather than a list that matches nothing. */}
                  <BrowserLinkSettings />
                  <BrowserTrustedSites />
                  <Row
                    label={t("settings.row.browserSites")}
                    hint={t("settings.hint.browserSites")}
                    applies={c.applies?.CHIMERA_BROWSER_SITES}
                    env="CHIMERA_BROWSER_SITES"
                  >
                    <BrowserSites
                      sites={c.browser?.sites ?? []}
                      onSave={(v) => save({ CHIMERA_BROWSER_SITES: v })}
                    />
                  </Row>
                  <Row label={t("settings.row.browserGoogle")} hint={t("settings.hint.browserGoogle")} applies={c.applies?.CHIMERA_BROWSER_GOOGLE} env="CHIMERA_BROWSER_GOOGLE">
                    <Select value={c.browser?.google ?? "off"} options={["off", "ask"]} render={(v) => v === "ask" ? t("settings.value.browserGoogleAsk") : t("settings.value.browserGoogleOff")} onChange={(v) => save({ CHIMERA_BROWSER_GOOGLE: v })} />
                  </Row>
                  <Row
                    label={t("settings.row.browserCookieRetention")}
                    hint={t("settings.hint.browserCookieRetention")}
                    applies={c.applies?.CHIMERA_BROWSER_COOKIE_RETENTION}
                    env="CHIMERA_BROWSER_COOKIE_RETENTION"
                  >
                    <Select
                      value={c.browser?.cookie_retention ?? "ephemeral"}
                      options={["shared", "conversation", "ephemeral"]}
                      render={(v) =>
                        v === "shared"
                          ? t("settings.value.browserCookiesShared")
                          : v === "conversation"
                            ? t("settings.value.browserCookiesConversation")
                            : t("settings.value.browserCookiesEphemeral")
                      }
                      onChange={(v) => save({ CHIMERA_BROWSER_COOKIE_RETENTION: v })}
                    />
                  </Row>
                  <Row
                    label={t("settings.row.browserLocalPorts")}
                    hint={t("settings.hint.browserLocalPorts")}
                    applies={c.applies?.CHIMERA_BROWSER_LOCAL_PORTS}
                    env="CHIMERA_BROWSER_LOCAL_PORTS"
                    warn
                  >
                    <TextField
                      value={(c.browser?.local_ports ?? []).join(", ")}
                      placeholder="3000, 8080"
                      onSave={(v) => save({ CHIMERA_BROWSER_LOCAL_PORTS: v })}
                    />
                  </Row>
                  {/* A hand-edited `.env` value that does not parse leaves the browser out of every
              conversation. The two rows above would then read empty — "any public site" — so the
              server's reason is shown here, in its own words (it names the key and the entry). */}
                  {c.browser?.invalid ? (
                    <p role="alert" className="text-xs text-bad-foreground">
                      {t("settings.browserReach.invalid", { error: c.browser.invalid })}
                    </p>
                  ) : null}
                  {/* The switch the posture line names when it reports a conversation as unguarded. Off
              by default (`chimera/config.py`: `guard_chat: bool = Field(default=False)`), which is
              a real exposure and the reason the posture line has to say so.

              It used to read "off by default because this registry is shared with the messaging
              gateway: arming it silently would take shell away from agents someone already runs in
              Discord." That was never true. `MessagingManager._gateway_on_message`
              (`chimera/server/manager.py:139-166`) builds its OWN registry through
              `governed_profile(..., surface="app-messaging")`, its own Agent and its own
              ChatSession — nothing about a Discord bot changes when this switch moves. A false
              justification is worse than none: it makes a permissive default look considered. */}
                  <Row
                    label={t("settings.row.guardChat")}
                    hint={t("settings.hint.guardChat")}
                    applies={c.applies?.CHIMERA_GUARD_CHAT}
                    env="CHIMERA_GUARD_CHAT"
                  >
                    <Toggle
                      on={c.guard.chat}
                      onChange={(v) => save({ CHIMERA_GUARD_CHAT: String(v) })}
                    />
                  </Row>
                </Card>

                <Card title={t("settings.card.mcp")}>
                  <Row
                    label={t("settings.row.mcpAutoload")}
                    hint={t("settings.hint.mcpAutoload")}
                    applies={c.applies?.CHIMERA_MCP_AUTOLOAD}
                    env="CHIMERA_MCP_AUTOLOAD"
                  >
                    <Toggle
                      on={c.mcp.autoload}
                      onChange={(v) =>
                        save({ CHIMERA_MCP_AUTOLOAD: String(v) })
                      }
                    />
                  </Row>
                </Card>

                {/* No row for CHIMERA_APPROVE_VIA_CHAT, by design: approving from a chat channel widens who can approve, so enabling it stays a deliberate .env edit by the owner. */}
                <Card title={t("settings.card.automation")}>
                  <Row
                    label={t("settings.row.appCron")}
                    hint={t("settings.hint.appCron")}
                    applies={c.applies?.CHIMERA_APP_CRON}
                    env="CHIMERA_APP_CRON"
                  >
                    <Toggle
                      on={c.automation.cron}
                      onChange={(v) => save({ CHIMERA_APP_CRON: String(v) })}
                    />
                  </Row>
                  {/* On unless the owner turned it off; a server without the field is on. */}
                  <Row
                    label={t("settings.row.cronNotifyFailures")}
                    hint={t("settings.hint.cronNotifyFailures")}
                    applies={c.applies?.CHIMERA_CRON_NOTIFY_FAILURES}
                    env="CHIMERA_CRON_NOTIFY_FAILURES"
                  >
                    <Toggle
                      on={c.automation.notify_failures ?? true}
                      onChange={(v) => save({ CHIMERA_CRON_NOTIFY_FAILURES: String(v) })}
                    />
                  </Row>
                  <WeeklyReviewRow />
                </Card>

                {/* Beside the scheduler because a schedule is the work a sleeping laptop loses most
              often. Off by default; a server without the block reads as off, which is what it does. */}
                <KeepAwakeCard
                  mode={c.keep_awake?.mode ?? "off"}
                  onBattery={c.keep_awake?.on_battery ?? false}
                  onSave={save}
                />

                {/* Three modules whose measurements did not recommend them, which is why each is off
              and why each hint says what was measured rather than what the module promises. A
              server that predates the `experimental` block reads as all three off: that is the
              shipped behaviour, and showing a switch as on for a module that is not running would
              be the one lie this card exists to avoid. */}
                <Card title={t("settings.card.experimental")}>
                  <Row
                    label={t("settings.row.browserSituation")}
                    hint={t("settings.hint.browserSituation")}
                    applies={c.applies?.CHIMERA_BROWSER_SITUATION}
                    env="CHIMERA_BROWSER_SITUATION"
                  >
                    <Toggle
                      on={c.experimental?.browser_situation ?? false}
                      onChange={(v) =>
                        save({ CHIMERA_BROWSER_SITUATION: String(v) })
                      }
                    />
                  </Row>
                  <Row
                    label={t("settings.row.researchAgent")}
                    hint={t("settings.hint.researchAgent")}
                    applies={c.applies?.CHIMERA_RESEARCH_AGENT}
                    env="CHIMERA_RESEARCH_AGENT"
                  >
                    <Toggle
                      on={c.experimental?.research_agent ?? false}
                      onChange={(v) => save({ CHIMERA_RESEARCH_AGENT: String(v) })}
                    />
                  </Row>
                  <Row
                    label={t("settings.row.explorerContract")}
                    hint={t("settings.hint.explorerContract")}
                    applies={c.applies?.CHIMERA_EXPLORER_CONTRACT}
                    env="CHIMERA_EXPLORER_CONTRACT"
                  >
                    <Toggle
                      on={c.experimental?.explorer_contract ?? false}
                      onChange={(v) =>
                        save({ CHIMERA_EXPLORER_CONTRACT: String(v) })
                      }
                    />
                  </Row>
                  {/* Tools reached on demand. Off: the built-in bench was inconclusive and the MCP
                half was never measured — the hints quote `bench/tool_defer/RESULT.md` and say so.
                Under each, the token half as THIS install measures it, a loss included. */}
                  <Row
                    label={t("settings.row.deferTools")}
                    hint={t("settings.hint.deferTools")}
                    note={<DeferSavingNote half="builtin" />}
                    applies={c.applies?.CHIMERA_DEFER_TOOLS}
                    env="CHIMERA_DEFER_TOOLS"
                  >
                    <Toggle
                      on={c.defer?.tools ?? false}
                      onChange={(v) => save({ CHIMERA_DEFER_TOOLS: String(v) })}
                    />
                  </Row>
                  <Row
                    label={t("settings.row.mcpDefer")}
                    hint={t("settings.hint.mcpDefer")}
                    note={<DeferSavingNote half="mcp" />}
                    applies={c.applies?.CHIMERA_MCP_DEFER}
                    env="CHIMERA_MCP_DEFER"
                  >
                    <Toggle
                      on={c.defer?.mcp ?? false}
                      onChange={(v) => save({ CHIMERA_MCP_DEFER: String(v) })}
                    />
                  </Row>
                  {/* A project's pack narrowing skills, servers and tools (study 29, P7.6). Off until
                the registered comparison runs; each pack still needs its own yes on the Code
                screen. The bridge cannot write this one — off is the direction that widens. */}
                  <Row
                    label={t("settings.row.projectPack")}
                    hint={t("settings.hint.projectPack")}
                    env="CHIMERA_PROJECT_PACK"
                  >
                    <Toggle
                      on={c.project_pack?.enabled ?? false}
                      onChange={(v) => save({ CHIMERA_PROJECT_PACK: String(v) })}
                    />
                  </Row>
                </Card>

                {/* Which instrument answers a typed decision. A server without the `decisions` block
              is on the shipped default, local — the same thing the card shows for it. */}
                <SystemOneCard
                  backend={c.decisions?.backend ?? "local_logprob"}
                  model={c.decisions?.model ?? ""}
                  verifiedAnswers={c.decisions?.verified_answers ?? true}
                  applies={c.applies?.CHIMERA_DECISION_BACKEND}
                  onSave={save}
                />

                {/* The desktop bridge (`chimera/api/desktop_bridge.py`): whether Claude, through
              `chimera mcp desktop`, may operate this running app. Both off by default. The second
              switch is disabled rather than hidden while the first is off, so the person can read
              what it would allow before deciding anything; its hint is a warning because what it
              adds is the one thing the first leaves with the owner — answering approvals. Saving
              either applies at once: the server writes or deletes the discovery file on the PATCH. */}
                <Card title={t("settings.card.bridge")}>
                  <Row
                    label={t("settings.row.desktopBridge")}
                    hint={t("settings.hint.desktopBridge")}
                    env="CHIMERA_DESKTOP_BRIDGE"
                  >
                    <Toggle
                      on={c.bridge?.enabled ?? false}
                      onChange={(v) => save({ CHIMERA_DESKTOP_BRIDGE: String(v) })}
                    />
                  </Row>
                  <Row
                    label={t("settings.row.desktopBridgeFull")}
                    hint={t("settings.hint.desktopBridgeFull")}
                    env="CHIMERA_DESKTOP_BRIDGE_FULL"
                    warn
                  >
                    <Switch
                      checked={c.bridge?.full ?? false}
                      disabled={!(c.bridge?.enabled ?? false)}
                      label={t("settings.row.desktopBridgeFull")}
                      onChange={(v) => save({ CHIMERA_DESKTOP_BRIDGE_FULL: String(v) })}
                    />
                  </Row>
                  <p className="px-4 py-2.5 text-xs text-muted-foreground">
                    {c.bridge?.enabled
                      ? t("settings.note.desktopBridgeSetup")
                      : t("settings.note.desktopBridgeNeedsFirst")}
                  </p>
                </Card>

                {/* What this install keeps on disk, and where a run's worktree goes (study 29,
              P5.3). Measured, never guessed: a row the server could not count says so. */}
                <StorageCard
                  worktreeDir={c.storage?.worktree_dir ?? ""}
                  branchPrefix={c.storage?.branch_prefix ?? "chimera"}
                  onSave={save}
                />

                <DiagnosticsCard />

                <Card title={t("settings.card.server")}>
                  <Row
                    label={t("settings.row.bearer")}
                    hint={t("settings.hint.bearer")}
                    env="CHIMERA_SERVER_TOKEN"
                  >
                    <SecretField
                      // Not a provider — SecretField's masked-write behaviour is what is being reused. The
                      // model/keys_url fields are empty because there is nothing on the other end of this
                      // token to suggest a model for or to send anyone to sign up at.
                      provider={{
                        env: "CHIMERA_SERVER_TOKEN",
                        // Not a routing name either — nothing serves models on the other end of this token.
                        name: "",
                        label: "token",
                        set: c.server.token_set,
                        hint: "",
                        llm: false,
                        model: "",
                        keys_url: "",
                        // This screen never puts the token in the vault (the tray reads it from
                        // `.env`); `chimera secrets set` can, and then the row says where it is.
                        in_vault: (c.vault?.keys ?? []).includes("CHIMERA_SERVER_TOKEN"),
                      }}
                      onSave={(v) => save({ CHIMERA_SERVER_TOKEN: v })}
                    />
                  </Row>
                </Card>

                {/* The two settings that narrow sharing (`chimera/api/sharing.py`). On and empty are
              what sharing did before they existed. Which links exist, and revoking them, is the
              access card on the Security tab. Neither row is writable through the bridge: their
              other direction widens. */}
                <Card title={t("settings.card.sharing")}>
                  <Row
                    label={t("settings.row.sharing")}
                    hint={t("settings.hint.sharing")}
                    env="CHIMERA_SHARING"
                  >
                    <Toggle
                      on={c.sharing?.enabled ?? true}
                      onChange={(v) => save({ CHIMERA_SHARING: String(v) })}
                    />
                  </Row>
                  <Row
                    label={t("settings.row.shareExpiry")}
                    hint={t("settings.hint.shareExpiry")}
                    env="CHIMERA_SHARE_EXPIRY_HOURS"
                  >
                    <TextField
                      value={c.sharing?.expiry_hours ? String(c.sharing.expiry_hours) : ""}
                      placeholder={t("settings.placeholder.shareExpiry")}
                      onSave={(v) => save({ CHIMERA_SHARE_EXPIRY_HOURS: v.trim() })}
                    />
                  </Row>
                </Card>

                {mutation.isError && (
                  <p className="text-sm text-bad-foreground">{t("settings.saveError")}</p>
                )}
                  </div>
                </details>
                </section>
              </div>
            )}
          </TabPanel>}
        </div>
        </div>
      </div>
    </PinnedContext.Provider>
  );
}
