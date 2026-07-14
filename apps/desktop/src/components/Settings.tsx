import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, KeyRound, Loader2 } from "lucide-react";
import { getConfig, getDoctor, patchConfig } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { LANGS, useI18n, useT } from "@/lib/i18n";
import type { AppConfig, DoctorInfo, ProviderCfg } from "@/lib/types";

function Card({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="surface overflow-hidden">
      <h2 className="border-b border-white/5 px-4 py-2.5 text-sm font-semibold">{title}</h2>
      <div className="divide-y divide-white/[0.04]">{children}</div>
    </section>
  );
}

function Row({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-4 px-4 py-3">
      <div className="min-w-0">
        <div className="text-sm font-medium">{label}</div>
        {hint && <div className="text-xs text-muted-foreground">{hint}</div>}
      </div>
      <div className="flex shrink-0 items-center gap-2">{children}</div>
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
  return (
    <button
      onClick={() => onChange(!on)}
      className={`relative h-5 w-9 rounded-chip transition-all ${
        on
          ? "bg-accent-grad shadow-[0_0_12px_-2px_hsl(var(--accent)/0.75)]"
          : "bg-muted shadow-inset"
      }`}
      role="switch"
      aria-checked={on}
    >
      <span
        className={`absolute top-0.5 h-4 w-4 rounded-full bg-white shadow-sm transition-all ${
          on ? "left-4" : "left-0.5"
        }`}
      />
    </button>
  );
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
  return (
    <select className={inputCls} value={value} onChange={(e) => onChange(e.target.value)}>
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

export function Settings() {
  const t = useT();
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

  if (config.isLoading || !config.data) {
    return (
      <div className="flex flex-1 items-center justify-center text-muted-foreground">
        <Loader2 className="h-5 w-5 animate-spin" />
      </div>
    );
  }
  const c: AppConfig = config.data;
  const d: DoctorInfo | undefined = doctor.data;

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="mx-auto max-w-2xl space-y-6 px-6 py-6">
        <div className="flex items-center gap-2">
          <KeyRound className="h-5 w-5" />
          <h1 className="text-lg font-semibold">{t("settings.title")}</h1>
          {mutation.isPending && <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />}
        </div>

        <Card title={t("settings.card.appearance")}>
          <Row label={t("settings.row.language")} hint={t("settings.hint.language")}>
            <LanguageSelect />
          </Row>
        </Card>

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
          <Row label={t("settings.row.cascade")} hint={t("settings.hint.cascade")}>
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
        </Card>

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
        </Card>

        <Card title={t("settings.card.mcp")}>
          <Row label={t("settings.row.mcpAutoload")} hint={t("settings.hint.mcpAutoload")}>
            <Toggle
              on={c.mcp.autoload}
              onChange={(v) => save({ CHIMERA_MCP_AUTOLOAD: String(v) })}
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
    </div>
  );
}
