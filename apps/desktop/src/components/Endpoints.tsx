import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Plus, Trash2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  addEndpoint,
  listEndpoints,
  removeEndpoint,
  testEndpoint,
  type NamedEndpointTest,
} from "@/lib/api";
import { useT } from "@/lib/i18n";

const inputCls = "w-full bg-transparent text-sm outline-hidden";

/**
 * Named OpenAI-compatible endpoints (#1133): a vLLM, LM Studio or llama.cpp server, or a hosted one
 * other than OpenRouter, each with its own base URL and key.
 *
 * What this card does NOT hold: a key. The form asks for the NAME of the environment variable that
 * carries the key, and the list shows only a host and whether that variable is set. Nothing a key
 * could be read from is ever in this component's state, a request body, or a response this screen
 * renders. The URL and name rules are the backend's (`validate_endpoints`), so a refusal arrives as
 * the server's reason rather than a second copy of the rules kept here.
 */
export function NamedEndpoints() {
  const t = useT();
  const qc = useQueryClient();
  const [adding, setAdding] = useState(false);
  const [draft, setDraft] = useState({ name: "", base_url: "", key_env: "" });
  const [formError, setFormError] = useState("");
  const [results, setResults] = useState<Record<string, NamedEndpointTest>>({});

  const list = useQuery({ queryKey: ["endpoints"], queryFn: listEndpoints });
  const endpoints = list.data?.endpoints ?? [];

  const refresh = () => qc.invalidateQueries({ queryKey: ["endpoints"] });

  const add = useMutation({
    mutationFn: () => addEndpoint({ name: draft.name.trim(), base_url: draft.base_url.trim(), key_env: draft.key_env.trim() }),
    onSuccess: () => {
      setDraft({ name: "", base_url: "", key_env: "" });
      setFormError("");
      setAdding(false);
      void refresh();
    },
    // The server's own reason: "base_url must use https", "key_env must be a valid environment variable
    // name", "already exists". Shown as it is, because it is the one place those rules are written.
    onError: (e: unknown) => setFormError(e instanceof Error ? e.message : t("endpoints.addFailed")),
  });

  const test = useMutation({
    mutationFn: (name: string) => testEndpoint(name),
    onSuccess: (result) => setResults((prev) => ({ ...prev, [result.name]: result })),
    onError: (e: unknown, name) =>
      setResults((prev) => ({
        ...prev,
        [name]: { name, host: "", ok: false, models: null, error: e instanceof Error ? e.message : "" },
      })),
  });

  const remove = useMutation({
    mutationFn: (name: string) => removeEndpoint(name),
    onSuccess: (_data, name) => {
      setResults((prev) => {
        const next = { ...prev };
        delete next[name];
        return next;
      });
      void refresh();
    },
  });

  const canSave = draft.name.trim() !== "" && draft.base_url.trim() !== "" && draft.key_env.trim() !== "";

  return (
    <section id="settings-card-endpoints" aria-labelledby="settings-card-endpoints-title" className="grid gap-3">
      <div>
        <h3 id="settings-card-endpoints-title" className="text-sm font-medium">
          {t("settings.card.endpoints")}
        </h3>
        <p className="mt-1 max-w-prose text-xs text-muted-foreground">{t("endpoints.body")}</p>
      </div>

      {list.isError ? (
        <p className="text-xs text-bad-foreground">{t("endpoints.loadFailed")}</p>
      ) : endpoints.length === 0 ? (
        <p className="text-xs text-muted-foreground">{t("endpoints.empty")}</p>
      ) : (
        <ul className="grid gap-2">
          {endpoints.map((endpoint) => {
            const result = results[endpoint.name];
            return (
              <li key={endpoint.name} className="rounded-chip border border-hairline px-3 py-2">
                <div className="flex items-center gap-3">
                  <div className="min-w-0 flex-1">
                    <p className="truncate font-mono text-sm">{endpoint.name}</p>
                    <p className="truncate font-mono text-xs text-muted-foreground">{endpoint.host}</p>
                    <p className="text-xs text-muted-foreground">
                      {endpoint.key_configured ? (
                        <span className="inline-flex items-center gap-1 text-ok-foreground">
                          <Check className="h-3 w-3" aria-hidden />
                          {t("endpoints.keySet")}
                        </span>
                      ) : (
                        <span className="text-warn-foreground">{t("endpoints.keyMissing")}</span>
                      )}
                    </p>
                  </div>
                  <Button
                    size="sm"
                    variant="ghost"
                    disabled={test.isPending}
                    onClick={() => test.mutate(endpoint.name)}
                  >
                    {t("endpoints.test")}
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    aria-label={t("endpoints.remove", { name: endpoint.name })}
                    disabled={remove.isPending}
                    onClick={() => remove.mutate(endpoint.name)}
                  >
                    <Trash2 className="h-3.5 w-3.5" aria-hidden />
                  </Button>
                </div>
                {result && (
                  <p
                    role="status"
                    className={`mt-1 text-xs ${result.ok ? "text-ok-foreground" : "text-bad-foreground"}`}
                  >
                    {result.ok
                      ? t("endpoints.testOk", { models: result.models ?? 0 })
                      : t("endpoints.testFailed", { error: result.error ?? "" })}
                  </p>
                )}
              </li>
            );
          })}
        </ul>
      )}

      {adding ? (
        <form
          className="grid gap-2 rounded-chip border border-hairline p-3"
          onSubmit={(e) => {
            e.preventDefault();
            if (canSave) add.mutate();
          }}
        >
          <label className="grid gap-1 text-xs text-muted-foreground">
            {t("endpoints.name")}
            <input
              className={inputCls}
              value={draft.name}
              placeholder="vllm-lab"
              onChange={(e) => setDraft({ ...draft, name: e.target.value })}
            />
          </label>
          <label className="grid gap-1 text-xs text-muted-foreground">
            {t("endpoints.baseUrl")}
            <input
              className={`${inputCls} font-mono`}
              value={draft.base_url}
              placeholder="https://llm.exemplo.com/v1"
              onChange={(e) => setDraft({ ...draft, base_url: e.target.value })}
            />
            <span className="text-xs text-muted-foreground">{t("endpoints.urlHint")}</span>
          </label>
          <label className="grid gap-1 text-xs text-muted-foreground">
            {t("endpoints.keyEnv")}
            <input
              className={`${inputCls} font-mono`}
              value={draft.key_env}
              placeholder="VLLM_API_KEY"
              onChange={(e) => setDraft({ ...draft, key_env: e.target.value })}
            />
            <span className="text-xs text-muted-foreground">{t("endpoints.keyHint")}</span>
          </label>
          {formError && <p className="text-xs text-bad-foreground">{formError}</p>}
          <div className="flex justify-end gap-2">
            <Button size="sm" variant="ghost" type="button" onClick={() => { setAdding(false); setFormError(""); }}>
              {t("endpoints.cancel")}
            </Button>
            <Button size="sm" type="submit" disabled={!canSave || add.isPending}>
              {t("endpoints.save")}
            </Button>
          </div>
        </form>
      ) : (
        <div>
          <Button size="sm" variant="ghost" onClick={() => setAdding(true)}>
            <Plus className="h-3.5 w-3.5" aria-hidden />
            {t("endpoints.add")}
          </Button>
        </div>
      )}
    </section>
  );
}
