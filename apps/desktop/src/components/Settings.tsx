import { createContext, useContext, useId, useState, type ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, KeyRound, Loader2 } from "lucide-react";
import {
  getConfig,
  getDoctor,
  getInstructions,
  getMessaging,
  patchConfig,
  putInstructions,
  startMessaging,
  stopMessaging,
} from "@/lib/api";
import { Button } from "@/components/ui/button";
import { ErrorState } from "@/components/ui/async";
import { Switch } from "@/components/ui/switch";
import { Tabs, TabPanel } from "@/components/ui/tabs";
import { Connections } from "@/components/Connections";
import { Governance } from "@/components/Governance";
import { Usage } from "@/components/Usage";
import { LANGS, useI18n, useT } from "@/lib/i18n";
import type { AgentIdentity, AppConfig, DoctorInfo, ProviderCfg } from "@/lib/types";

function Card({ title, children }: { title: string; children: ReactNode }) {
  // Named region rather than a bare section: this screen stacks eleven of them, and unnamed they
  // announce as eleven identical "section" landmarks with no way to tell which one you are in.
  const headingId = useId();
  return (
    <section className="surface overflow-hidden" aria-labelledby={headingId}>
      <h2
        id={headingId}
        className="border-b border-hairline px-4 py-2.5 text-sm font-semibold"
      >
        {title}
      </h2>
      <div className="divide-y divide-hairline">{children}</div>
    </section>
  );
}

/** The label of the enclosing Row, so a control inside it can name itself without repeating it. */
const RowLabelContext = createContext("");

/** When a saved change starts applying, for the settings where the answer is not "the next call".
 *
 * The value comes from the server (`config.applies`), never from a list kept here: the answer is a
 * property of where the setting is READ, so a copy on this side would go stale the first time a read
 * moves — and it would go stale silently, which is the whole failure this label exists to stop. A
 * control that confirms and does nothing spends the user's trust in every other control on screen.
 */
function AppliesNote({ when }: { when?: string }) {
  const t = useT();
  if (when !== "next_conversation" && when !== "next_launch") return null;
  return (
    <div className="text-xs text-warn">
      {t(when === "next_launch" ? "settings.applies.nextLaunch" : "settings.applies.nextConversation")}
    </div>
  );
}

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
  const saved = useQuery({ queryKey: ["instructions"], queryFn: getInstructions });
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

  const current = draft ?? saved.data ?? { name: "", language: "", instructions: "" };
  const dirty = draft !== null && JSON.stringify(draft) !== JSON.stringify(saved.data);
  const edit = (patch: Partial<AgentIdentity>) => setDraft({ ...current, ...patch });
  const uiLanguage = LANGS.find((l) => l.code === lang)?.label ?? "";

  return (
    <Card title={t("settings.card.agent")}>
      <Row label={t("settings.row.agentName")} hint={t("settings.hint.agentName")}>
        <input
          className={inputCls}
          value={current.name}
          placeholder="Chimera"
          aria-label={t("settings.row.agentName")}
          onChange={(e) => edit({ name: e.target.value })}
        />
      </Row>
      <Row label={t("settings.row.agentLanguage")} hint={t("settings.hint.agentLanguage")}>
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
          <div className="text-sm font-medium">{t("settings.row.agentInstructions")}</div>
          <div className="text-xs text-muted-foreground">{t("settings.hint.agentInstructions")}</div>
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
        <p className="text-xs text-muted-foreground">{t("settings.hint.agentNoGrant")}</p>
        <div className="flex items-center gap-2">
          <Button size="sm" disabled={!dirty || mutation.isPending} onClick={() => mutation.mutate(current)}>
            {t("common.save")}
          </Button>
          {dirty && <span className="text-xs text-warn">{t("settings.unsaved")}</span>}
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
function AutonomyCard({ c, save }: { c: AppConfig; save: (u: Record<string, string>) => void }) {
  const t = useT();
  const [confirmingHostExec, setConfirmingHostExec] = useState(false);

  return (
    <Card title={t("settings.card.autonomy")}>
      <Row label={t("settings.row.reach")} hint={t("settings.hint.reach")}>
        <Select
          value={c.autonomy.reach || "unset"}
          options={["unset", "read_only", "workspace", "workspace_shell"]}
          onChange={(v) => save({ CHIMERA_REACH: v === "unset" ? "" : v })}
        />
      </Row>
      <Row label={t("settings.row.approval")} hint={t("settings.hint.approval")}>
        <Select
          value={c.autonomy.approval || "unset"}
          options={["unset", "always", "suspicious", "never"]}
          onChange={(v) => save({ CHIMERA_APPROVAL: v === "unset" ? "" : v })}
        />
      </Row>
      <Row label={t("settings.row.hostExec")} hint={t("settings.hint.hostExec")}>
        <Select
          value={c.autonomy.host_exec}
          options={["ask", "deny", "allow"]}
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
          <p className="text-xs text-bad">{t("settings.hostExec.warning")}</p>
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
            <Button size="sm" variant="ghost" onClick={() => setConfirmingHostExec(false)}>
              {t("common.cancel")}
            </Button>
          </div>
        </div>
      )}
      {(c.autonomy.denied_tools ?? []).length > 0 && (
        <Row label={t("settings.row.deniedTools")} hint={t("settings.hint.deniedTools")}>
          <span className="max-w-56 truncate font-mono text-xs text-muted-foreground">
            {(c.autonomy.denied_tools ?? []).join(", ")}
          </span>
        </Row>
      )}
    </Card>
  );
}

function Row({
  label,
  hint,
  applies,
  children,
}: {
  label: string;
  hint?: string;
  applies?: string;
  children: ReactNode;
}) {
  return (
    <div className="flex items-center justify-between gap-4 px-4 py-3">
      <div className="min-w-0">
        <div className="text-sm font-medium">{label}</div>
        {hint && <div className="text-xs text-muted-foreground">{hint}</div>}
        <AppliesNote when={applies} />
      </div>
      <div className="flex shrink-0 items-center gap-2">
        <RowLabelContext.Provider value={label}>{children}</RowLabelContext.Provider>
      </div>
    </div>
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
        onChange={(e) => setV(e.target.value)}
      />
      <Button size="sm" disabled={!dirty} onClick={() => onSave(v)}>
        {t("common.save")}
      </Button>
    </>
  );
}

function LanguageSelect() {
  const { lang, setLang } = useI18n();
  return (
    <select
      className={inputCls}
      value={lang}
      onChange={(e) => setLang(e.target.value as (typeof LANGS)[number]["code"])}
    >
      {LANGS.map((l) => (
        <option key={l.code} value={l.code}>
          {l.label}
        </option>
      ))}
    </select>
  );
}

function Toggle({ on, onChange }: { on: boolean; onChange: (v: boolean) => void }) {
  // Was a near-copy of the one in Cron.tsx. Now the shared primitive, named from its Row — before
  // this, all six of these announced as an unlabelled "switch".
  return <Switch checked={on} onChange={onChange} label={useContext(RowLabelContext)} />;
}

function Select({
  value,
  options,
  onChange,
}: {
  value: string;
  options: string[];
  onChange: (v: string) => void;
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
          {o}
        </option>
      ))}
    </select>
  );
}

function SecretField({ provider, onSave }: { provider: ProviderCfg; onSave: (v: string) => void }) {
  const t = useT();
  const [editing, setEditing] = useState(false);
  const [v, setV] = useState("");
  if (!editing) {
    return (
      <>
        {provider.set ? (
          <span className="flex items-center gap-1 text-xs text-muted-foreground">
            <Check className="h-3.5 w-3.5 text-ok" /> {t("settings.isSet")} {provider.hint}
          </span>
        ) : (
          <span className="text-xs text-muted-foreground">{t("settings.notSet")}</span>
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
}: {
  save: (u: Record<string, string>) => void;
  platform?: "discord" | "telegram";
  tokenEnv?: string;
}) {
  const t = useT();
  const qc = useQueryClient();
  const status = useQuery({ queryKey: ["messaging"], queryFn: getMessaging });
  const [editing, setEditing] = useState(false);
  const [token, setToken] = useState("");
  const invalidate = () => qc.invalidateQueries({ queryKey: ["messaging"] });
  const toggle = useMutation({
    mutationFn: (on: boolean) => (on ? startMessaging(platform) : stopMessaging(platform)),
    onSuccess: invalidate,
  });

  const d = status.data?.[platform];
  const running = !!d?.running;
  const configured = !!d?.configured;
  const label = platform === "discord" ? "Discord" : "Telegram";

  return (
    <Card title={`${t("settings.card.messaging")} · ${label}`}>
      <Row
        label={t("settings.row.botToken", { platform: label })}
        hint={t("settings.hint.botToken", { platform: label })}
      >
        {editing ? (
          <>
            <input
              className={inputCls}
              type="password"
              autoFocus
              placeholder={t("settings.pasteKey")}
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
              <span className="text-xs text-muted-foreground">{t("settings.notSet")}</span>
            )}
            <Button size="sm" variant="outline" onClick={() => setEditing(true)}>
              {configured ? t("common.replace") : t("common.set")}
            </Button>
          </>
        )}
      </Row>
      <Row
        label={t("settings.row.botRun", { platform: label })}
        hint={t("settings.hint.botRun")}
      >
        <div className="flex items-center gap-2">
          {d?.error && !running && (
            <span className="max-w-[16rem] truncate text-xs text-bad" title={d.error}>
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
      {!configured && (
        <div className="px-4 pb-3 text-xs text-muted-foreground">{t("settings.messaging.note")}</div>
      )}
    </Card>
  );
}

type SettingsTab = "general" | "connections" | "usage" | "security";

export function Settings() {
  const t = useT();
  const tabsId = useId();
  const [tab, setTab] = useState<SettingsTab>("general");
  const qc = useQueryClient();
  const config = useQuery({ queryKey: ["config"], queryFn: getConfig });
  const doctor = useQuery({ queryKey: ["doctor"], queryFn: getDoctor });
  const mutation = useMutation({
    mutationFn: patchConfig,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["config"] });
      qc.invalidateQueries({ queryKey: ["doctor"] });
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

  const tabs = [
    { value: "general" as const, label: t("settings.tab.general") },
    { value: "connections" as const, label: t("settings.tab.connections") },
    { value: "usage" as const, label: t("nav.usage") },
    { value: "security" as const, label: t("settings.tab.security") },
  ];

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex items-center gap-2 px-6 pt-6">
        <KeyRound className="h-5 w-5 text-accent" />
        <h1 className="text-lg font-semibold">{t("settings.title")}</h1>
        {mutation.isPending && <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />}
      </div>
      <Tabs items={tabs} value={tab} onChange={setTab} aria-label={t("settings.title")} className="px-6" />
      <div className="min-h-0 flex-1 overflow-y-auto">
        <TabPanel tabsId={tabsId} value={tab}>
          {tab === "connections" && <Connections />}
          {tab === "usage" && <Usage embedded />}
          {tab === "security" && <Governance embedded />}
          {tab === "general" && (
      <div className="mx-auto max-w-2xl space-y-6 px-6 py-6">

        <Card title={t("settings.card.appearance")}>
          <Row label={t("settings.row.language")} hint={t("settings.hint.language")}>
            <LanguageSelect />
          </Row>
        </Card>

        <IdentityCard />

        <AutonomyCard c={c} save={save} />

        {d && (
          <Card title={t("settings.card.status")}>
            <Row label={t("settings.row.providersWithKey")}>
              <span className="text-sm">
                {d.configured_providers.length
                  ? d.configured_providers.join(", ")
                  : t("settings.none")}
              </span>
            </Row>
            <Row label={t("settings.row.modelLadder")} hint={t("settings.hint.modelLadder")}>
              <span className="max-w-56 truncate font-mono text-xs">
                {d.tiers.weak} · {d.tiers.mid} · {d.tiers.top}
              </span>
            </Row>
          </Card>
        )}

        <Card title={t("settings.card.model")}>
          <Row label={t("settings.row.defaultModel")}>
            <TextField
              value={c.models.default}
              placeholder="openrouter/…"
              onSave={(v) => save({ CHIMERA_DEFAULT_MODEL: v })}
            />
          </Row>
          <Row label={t("settings.row.costMode")} hint={t("settings.hint.costMode")}>
            <Select
              value={c.models.cost_mode}
              options={["auto", "cheap", "balanced", "premium"]}
              onChange={(v) => save({ CHIMERA_COST_MODE: v })}
            />
          </Row>
          <Row
            label={t("settings.row.cascade")}
            hint={t("settings.hint.cascade")}
            applies={c.applies?.CHIMERA_CASCADE}
          >
            <Toggle on={c.models.cascade} onChange={(v) => save({ CHIMERA_CASCADE: String(v) })} />
          </Row>
        </Card>

        <Card title={t("settings.card.apiKeys")}>
          {c.providers.map((p) => (
            <Row key={p.env} label={p.label} hint={p.env}>
              <SecretField provider={p} onSave={(v) => save({ [p.env]: v })} />
            </Row>
          ))}
        </Card>

        <Card title={t("settings.card.memory")}>
          <Row label={t("settings.row.backend")}>
            <Select
              value={c.memory.backend}
              options={["json", "sqlite"]}
              onChange={(v) => save({ CHIMERA_MEMORY_BACKEND: v })}
            />
          </Row>
          <Row label={t("settings.row.semantic")} hint={t("settings.hint.semantic")}>
            <Toggle
              on={c.memory.semantic}
              onChange={(v) => save({ CHIMERA_SEMANTIC_MEMORY: String(v) })}
            />
          </Row>
          <Row
            label={t("settings.row.rememberChat")}
            hint={t("settings.hint.rememberChat")}
            applies={c.applies?.CHIMERA_CHAT_MEMORY}
          >
            <Toggle
              on={c.memory.remember_from_chat}
              onChange={(v) => save({ CHIMERA_CHAT_MEMORY: String(v) })}
            />
          </Row>
        </Card>

        <MessagingCard save={save} />
        <MessagingCard
          save={save}
          platform="telegram"
          tokenEnv="CHIMERA_TELEGRAM_BOT_TOKEN"
        />

        <Card title={t("settings.card.cacheSandbox")}>
          <Row label={t("settings.row.completionCache")} hint={t("settings.hint.completionCache")}>
            <Toggle on={c.cache.completion} onChange={(v) => save({ CHIMERA_CACHE: String(v) })} />
          </Row>
          <Row label={t("settings.row.sandbox")}>
            <Select
              value={c.sandbox.mode}
              options={["local", "docker"]}
              onChange={(v) => save({ CHIMERA_SANDBOX: v })}
            />
          </Row>
          {/* The switch the posture line names when it reports a conversation as unguarded. Off by
              default because this registry is shared with the messaging gateway: arming it silently
              would take shell away from agents someone already runs in Discord. */}
          <Row
            label={t("settings.row.guardChat")}
            hint={t("settings.hint.guardChat")}
            applies={c.applies?.CHIMERA_GUARD_CHAT}
          >
            <Toggle on={c.guard.chat} onChange={(v) => save({ CHIMERA_GUARD_CHAT: String(v) })} />
          </Row>
        </Card>

        <Card title={t("settings.card.mcp")}>
          <Row
            label={t("settings.row.mcpAutoload")}
            hint={t("settings.hint.mcpAutoload")}
            applies={c.applies?.CHIMERA_MCP_AUTOLOAD}
          >
            <Toggle
              on={c.mcp.autoload}
              onChange={(v) => save({ CHIMERA_MCP_AUTOLOAD: String(v) })}
            />
          </Row>
        </Card>

        <Card title={t("settings.card.automation")}>
          <Row
            label={t("settings.row.appCron")}
            hint={t("settings.hint.appCron")}
            applies={c.applies?.CHIMERA_APP_CRON}
          >
            <Toggle
              on={c.automation.cron}
              onChange={(v) => save({ CHIMERA_APP_CRON: String(v) })}
            />
          </Row>
        </Card>

        <Card title={t("settings.card.server")}>
          <Row label={t("settings.row.bearer")} hint={t("settings.hint.bearer")}>
            <SecretField
              provider={{ env: "CHIMERA_SERVER_TOKEN", label: "token", set: c.server.token_set, hint: "" }}
              onSave={(v) => save({ CHIMERA_SERVER_TOKEN: v })}
            />
          </Row>
        </Card>

        {mutation.isError && <p className="text-sm text-bad">{t("settings.saveError")}</p>}
      </div>
          )}
        </TabPanel>
      </div>
    </div>
  );
}
