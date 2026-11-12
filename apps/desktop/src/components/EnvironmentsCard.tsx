import { useId, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Trash2 } from "lucide-react";

import { environmentName } from "@/components/code/EnvironmentPicker";
import { Button } from "@/components/ui/button";
import { Tooltip } from "@/components/ui/tooltip";
import {
  listCodeEnvironments,
  listWslDistros,
  removeCodeEnvironment,
  saveCodeEnvironment,
  type CodeEnvironments,
} from "@/lib/api";
import { useT } from "@/lib/i18n";

/** The empty option of the distro list: WSL's own default distro, which is a choice and not a blank. */
const DEFAULT_DISTRO = "";

/**
 * The places a conversation's commands can run, other than this machine.
 *
 * Saved on the server and chosen on the Code screen before a conversation's first message. A kind
 * this machine cannot run is not offered (WSL needs Windows). What the card says about WSL is the
 * part that matters: it is a toolchain, not a boundary, and the files stay on the Windows disk.
 * The bridge and a paired device cannot reach any of this; the server refuses them.
 */
export function EnvironmentsCard() {
  const t = useT();
  const headingId = useId();
  const qc = useQueryClient();
  const list = useQuery({ queryKey: ["code-environments", ""], queryFn: () => listCodeEnvironments() });
  const wslCan = list.data?.kinds.some((k) => k.kind === "wsl" && k.available) === true;
  const distros = useQuery({
    queryKey: ["wsl-distros"],
    queryFn: () => listWslDistros(),
    enabled: wslCan,
  });
  const [name, setName] = useState("");
  const [distro, setDistro] = useState(DEFAULT_DISTRO);
  const [failed, setFailed] = useState(false);

  const keep = {
    onSuccess: (next: CodeEnvironments) => {
      setFailed(false);
      qc.setQueryData(["code-environments", ""], next);
      void qc.invalidateQueries({ queryKey: ["code-environments"] });
    },
    onError: () => setFailed(true),
  };
  const add = useMutation({
    mutationFn: () =>
      saveCodeEnvironment({ name: name.trim(), kind: "wsl", params: distro ? { distro } : {} }),
    ...keep,
    onSuccess: (next: CodeEnvironments) => {
      keep.onSuccess(next);
      setName("");
    },
  });
  const remove = useMutation({ mutationFn: (id: string) => removeCodeEnvironment(id), ...keep });
  const saved = (list.data?.environments ?? []).filter((e) => !e.builtin);

  return (
    <section className="surface overflow-hidden" aria-labelledby={headingId} data-testid="environments-card">
      <h2 id={headingId} className="border-b border-hairline px-4 py-2.5 text-sm font-semibold">
        {t("env.settings.title")}
      </h2>
      <div className="divide-y divide-hairline">
        <p className="px-4 py-2.5 text-xs text-muted-foreground">{t("env.settings.hint")}</p>
        <p className="px-4 py-2.5 text-xs text-muted-foreground">{t("env.settings.filesNote")}</p>
        {saved.length === 0 ? (
          <p className="px-4 py-2.5 text-xs text-muted-foreground">{t("env.settings.empty")}</p>
        ) : (
          <ul className="divide-y divide-hairline">
            {saved.map((env) => (
              <li key={env.id} className="flex items-center gap-3 px-4 py-2.5">
                <div className="min-w-0 flex-1">
                  <div className="truncate text-sm">{environmentName(t, env)}</div>
                  <div className="truncate font-mono text-xs text-muted-foreground">
                    {t("env.kind.wsl")} · {env.params.distro || t("env.settings.distroDefault")}
                  </div>
                </div>
                <Tooltip label={t("env.settings.remove")}>
                  <Button
                    size="sm"
                    variant="ghost"
                    aria-label={`${t("env.settings.remove")}: ${env.name}`}
                    disabled={remove.isPending}
                    onClick={() => remove.mutate(env.id)}
                  >
                    <Trash2 className="h-4 w-4" />
                  </Button>
                </Tooltip>
              </li>
            ))}
          </ul>
        )}
        {list.data && !wslCan ? (
          <p className="px-4 py-2.5 text-xs text-muted-foreground">{t("env.settings.wslOnly")}</p>
        ) : null}
        {wslCan ? (
          <form
            className="flex flex-wrap items-end gap-3 px-4 py-3"
            onSubmit={(event) => {
              event.preventDefault();
              if (name.trim()) add.mutate();
            }}
          >
            <label className="flex flex-col gap-1 text-xs text-muted-foreground">
              {t("env.settings.name")}
              <input
                className="field h-8 w-48 px-2 text-sm"
                value={name}
                maxLength={80}
                onChange={(event) => setName(event.target.value)}
              />
            </label>
            <label className="flex flex-col gap-1 text-xs text-muted-foreground">
              {t("env.settings.distro")}
              <select
                className="field h-8 w-48 px-2 text-sm"
                value={distro}
                onChange={(event) => setDistro(event.target.value)}
              >
                <option value={DEFAULT_DISTRO}>{t("env.settings.distroDefault")}</option>
                {(distros.data?.distros ?? []).map((d) => (
                  <option key={d} value={d}>
                    {d}
                  </option>
                ))}
              </select>
            </label>
            <Button type="submit" size="sm" disabled={!name.trim() || add.isPending}>
              {t("env.settings.add")}
            </Button>
            {failed ? (
              <span role="alert" className="text-xs text-bad-foreground">
                {t("env.settings.error")}
              </span>
            ) : null}
          </form>
        ) : null}
      </div>
    </section>
  );
}
