import { useId, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Trash2 } from "lucide-react";

import { environmentName } from "@/components/code/EnvironmentPicker";
import { Button } from "@/components/ui/button";
import { Tooltip } from "@/components/ui/tooltip";
import {
  allowSshHost,
  forgetSshHost,
  listCodeEnvironments,
  listSshHosts,
  listWslDistros,
  removeCodeEnvironment,
  saveCodeEnvironment,
  type CodeEnvironment,
  type CodeEnvironments,
  type SshHosts,
} from "@/lib/api";
import { useT, type TFunc } from "@/lib/i18n";

/** The empty option of the distro list: WSL's own default distro, which is a choice and not a blank. */
const DEFAULT_DISTRO = "";

type Kind = "wsl" | "ssh";

/** What a saved environment points at, in one line: the distro, or `host:folder`. Never a secret: there is none. */
function describe(t: TFunc, env: CodeEnvironment): string {
  if (env.kind === "ssh") return `${t("env.kind.ssh")} · ${env.params.host}:${env.params.remote_path}`;
  return `${t("env.kind.wsl")} · ${env.params.distro || t("env.settings.distroDefault")}`;
}

/**
 * The places a conversation's commands can run, other than this machine.
 *
 * Saved on the server and chosen on the Code screen before a conversation's first message. A kind
 * this machine cannot run is not offered (WSL needs Windows, SSH needs an `ssh` program). What the
 * card says about each is the part that matters: neither is isolation. SSH names a host alias from
 * your `~/.ssh/config` that you also allowed here; no user, address, password or key is typed in this
 * app. The bridge and a paired device cannot reach any of this; the server refuses them.
 */
export function EnvironmentsCard() {
  const t = useT();
  const headingId = useId();
  const qc = useQueryClient();
  const list = useQuery({ queryKey: ["code-environments", ""], queryFn: () => listCodeEnvironments() });
  const can = (kind: Kind) => list.data?.kinds.some((k) => k.kind === kind && k.available) === true;
  const wslCan = can("wsl");
  const sshCan = can("ssh");
  const distros = useQuery({
    queryKey: ["wsl-distros"],
    queryFn: () => listWslDistros(),
    enabled: wslCan,
  });
  const hosts = useQuery({ queryKey: ["ssh-hosts"], queryFn: () => listSshHosts(), enabled: sshCan });
  const [kindChoice, setKindChoice] = useState<Kind>("wsl");
  const [name, setName] = useState("");
  const [distro, setDistro] = useState(DEFAULT_DISTRO);
  const [host, setHost] = useState("");
  const [remotePath, setRemotePath] = useState("");
  const [toAllow, setToAllow] = useState("");
  const [failed, setFailed] = useState(false);
  const [hostFailed, setHostFailed] = useState(false);

  const kinds: Kind[] = [...(wslCan ? (["wsl"] as const) : []), ...(sshCan ? (["ssh"] as const) : [])];
  const kind: Kind = kinds.includes(kindChoice) ? kindChoice : (kinds[0] ?? "wsl");
  const allowed = hosts.data?.allowed ?? [];
  const configured = hosts.data?.config_hosts ?? [];
  const offerable = configured.filter((h) => !allowed.includes(h));
  const ready = name.trim() !== "" && (kind === "wsl" || (host !== "" && remotePath.trim() !== ""));

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
      saveCodeEnvironment({
        name: name.trim(),
        kind,
        params:
          kind === "ssh"
            ? { host, remote_path: remotePath.trim() }
            : distro
              ? { distro }
              : {},
      }),
    ...keep,
    onSuccess: (next: CodeEnvironments) => {
      keep.onSuccess(next);
      setName("");
      setRemotePath("");
    },
  });
  const remove = useMutation({ mutationFn: (id: string) => removeCodeEnvironment(id), ...keep });
  const keepHosts = {
    onSuccess: (next: SshHosts) => {
      setHostFailed(false);
      qc.setQueryData(["ssh-hosts"], next);
      void qc.invalidateQueries({ queryKey: ["code-environments"] });
    },
    onError: () => setHostFailed(true),
  };
  const allow = useMutation({
    mutationFn: (alias: string) => allowSshHost(alias),
    ...keepHosts,
    onSuccess: (next: SshHosts) => {
      keepHosts.onSuccess(next);
      setToAllow("");
    },
  });
  const forget = useMutation({ mutationFn: (alias: string) => forgetSshHost(alias), ...keepHosts });
  const saved = (list.data?.environments ?? []).filter((e) => !e.builtin);

  return (
    <section id="settings-card-environments" className="surface overflow-hidden" aria-labelledby={headingId} data-testid="environments-card">
      <h2 id={headingId} className="border-b border-hairline px-4 py-2.5 text-sm font-semibold">
        {t("env.settings.title")}
      </h2>
      <div className="divide-y divide-hairline">
        <p className="px-4 py-2.5 text-xs text-muted-foreground">{t("env.settings.hint")}</p>
        <p className="px-4 py-2.5 text-xs text-muted-foreground">{t("env.settings.filesNote")}</p>
        {sshCan ? <p className="px-4 py-2.5 text-xs text-muted-foreground">{t("env.ssh.note")}</p> : null}
        {saved.length === 0 ? (
          <p className="px-4 py-2.5 text-xs text-muted-foreground">{t("env.settings.empty")}</p>
        ) : (
          <ul className="divide-y divide-hairline">
            {saved.map((env) => (
              <li key={env.id} className="flex items-center gap-3 px-4 py-2.5">
                <div className="min-w-0 flex-1">
                  <div className="truncate text-sm">{environmentName(t, env)}</div>
                  <div className="truncate font-mono text-xs text-muted-foreground">{describe(t, env)}</div>
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
        {list.data && !sshCan ? (
          <p className="px-4 py-2.5 text-xs text-muted-foreground">{t("env.ssh.unavailable")}</p>
        ) : null}
        {kinds.length > 0 ? (
          <form
            className="flex flex-wrap items-end gap-3 px-4 py-3"
            onSubmit={(event) => {
              event.preventDefault();
              if (ready) add.mutate();
            }}
          >
            {kinds.length > 1 ? (
              <label className="flex flex-col gap-1 text-xs text-muted-foreground">
                {t("env.settings.type")}
                <select
                  className="field h-8 w-32 px-2 text-sm"
                  value={kind}
                  onChange={(event) => setKindChoice(event.target.value as Kind)}
                >
                  {kinds.map((k) => (
                    <option key={k} value={k}>
                      {t(k === "ssh" ? "env.kind.ssh" : "env.kind.wsl")}
                    </option>
                  ))}
                </select>
              </label>
            ) : null}
            <label className="flex flex-col gap-1 text-xs text-muted-foreground">
              {t("env.settings.name")}
              <input
                className="field h-8 w-48 px-2 text-sm"
                value={name}
                maxLength={80}
                onChange={(event) => setName(event.target.value)}
              />
            </label>
            {kind === "wsl" ? (
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
            ) : (
              <>
                <label className="flex flex-col gap-1 text-xs text-muted-foreground">
                  {t("env.ssh.host")}
                  <select
                    className="field h-8 w-48 px-2 text-sm"
                    value={host}
                    onChange={(event) => setHost(event.target.value)}
                  >
                    <option value="">{t("env.ssh.hostPick")}</option>
                    {allowed
                      .filter((h) => configured.includes(h))
                      .map((h) => (
                        <option key={h} value={h}>
                          {h}
                        </option>
                      ))}
                  </select>
                </label>
                <label className="flex flex-col gap-1 text-xs text-muted-foreground">
                  {t("env.ssh.path")}
                  <input
                    className="field h-8 w-64 px-2 font-mono text-sm"
                    value={remotePath}
                    placeholder="/home/me/project"
                    maxLength={1024}
                    onChange={(event) => setRemotePath(event.target.value)}
                  />
                </label>
              </>
            )}
            <Button type="submit" size="sm" disabled={!ready || add.isPending}>
              {t("env.settings.add")}
            </Button>
            {failed ? (
              <span role="alert" className="text-xs text-bad-foreground">
                {t("env.settings.error")}
              </span>
            ) : null}
          </form>
        ) : null}
        {sshCan ? (
          <div id="settings-ssh-hosts" className="px-4 py-3" data-testid="ssh-hosts">
            <h3 className="text-sm font-semibold">{t("env.ssh.hosts.title")}</h3>
            <p className="mt-1 text-xs text-muted-foreground">{t("env.ssh.hosts.hint")}</p>
            {allowed.length === 0 ? (
              <p className="mt-2 text-xs text-muted-foreground">{t("env.ssh.hosts.empty")}</p>
            ) : (
              <ul className="mt-2 divide-y divide-hairline">
                {allowed.map((h) => (
                  <li key={h} className="flex items-center gap-3 py-1.5">
                    <span className="min-w-0 flex-1 truncate font-mono text-sm">{h}</span>
                    <Tooltip label={t("env.ssh.hosts.remove")}>
                      <Button
                        size="sm"
                        variant="ghost"
                        aria-label={`${t("env.ssh.hosts.remove")}: ${h}`}
                        disabled={forget.isPending}
                        onClick={() => forget.mutate(h)}
                      >
                        <Trash2 className="h-4 w-4" />
                      </Button>
                    </Tooltip>
                  </li>
                ))}
              </ul>
            )}
            <form
              className="mt-2 flex flex-wrap items-end gap-3"
              onSubmit={(event) => {
                event.preventDefault();
                if (toAllow) allow.mutate(toAllow);
              }}
            >
              <label className="flex flex-col gap-1 text-xs text-muted-foreground">
                {t("env.ssh.hosts.pick")}
                <select
                  className="field h-8 w-48 px-2 text-sm"
                  value={toAllow}
                  onChange={(event) => setToAllow(event.target.value)}
                >
                  <option value="">{t("env.ssh.hostPick")}</option>
                  {offerable.map((h) => (
                    <option key={h} value={h}>
                      {h}
                    </option>
                  ))}
                </select>
              </label>
              <Button type="submit" size="sm" disabled={!toAllow || allow.isPending}>
                {t("env.ssh.hosts.add")}
              </Button>
              {hostFailed ? (
                <span role="alert" className="text-xs text-bad-foreground">
                  {t("env.ssh.hosts.error")}
                </span>
              ) : null}
            </form>
          </div>
        ) : null}
      </div>
    </section>
  );
}
