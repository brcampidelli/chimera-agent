import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { ResponseStyleNote } from "@/components/ResponseStyleNote";
import { ErrorState } from "@/components/ui/async";
import { Button } from "@/components/ui/button";
import { Badge, EmptyState, Panel, Spinner } from "@/components/ui/panel";
import {
  editMemory,
  forgetCodeProject,
  getInstructions,
  getMemoryLayers,
  getMemoryProfile,
  listCodeProjects,
  putInstructions,
} from "@/lib/api";
import { LANGS, useI18n, useT } from "@/lib/i18n";
import { memorySourceLabel } from "@/lib/memorySource";
import { listedProjects, projectLabel } from "@/lib/projects";

/**
 * What the agent has learned about you.
 *
 * `GET /api/memory/profile` existed and no screen read it, while Memory happily wrote persona facts
 * into the same store — so you could feed a picture of yourself that you were never shown. For a
 * feature whose entire premise is "it remembers you", not being able to see what it remembers is
 * the part that matters most.
 *
 * ONE list. The route sends the persona facts twice: `profile` is the ranked block the agent gets in
 * its prompt (each fact learned from untrusted content carries an English label addressed to the
 * MODEL), and `persona` is the same rows as records. The screen used to draw both, under two
 * headings, so every fact appeared twice and the model's label leaked onto a translated screen. The
 * records are what a person needs — the text, where it came from, whether it is verified — so they
 * are all this renders; `profile` stays on the wire for the readers it was written for.
 *
 * The answer language and the persona facts are edited here through the routes Settings and Memory
 * already use. The response style is shown, not edited: it belongs to a conversation, so an
 * account-wide copy would be a preference nothing reads. The projects are the registered ones, the
 * same list the sidebar draws.
 */
export function Profile({ onOpenMemory }: { onOpenMemory?: () => void } = {}) {
  const t = useT();
  const { lang } = useI18n();
  const qc = useQueryClient();
  const identity = useQuery({ queryKey: ["instructions"], queryFn: getInstructions });
  const projects = useQuery({ queryKey: ["code-projects"], queryFn: listCodeProjects });
  const profile = useQuery({ queryKey: ["memory-profile"], queryFn: getMemoryProfile });
  // Only to tell "nothing stored at all" from "nothing stored about YOU": the two need different
  // advice, and the second is the one where the facts already exist in Memory under another kind.
  const layers = useQuery({ queryKey: ["memory-layers"], queryFn: getMemoryLayers });
  const editFact = useMutation({
    mutationFn: ({ id, content }: { id: string; content: string }) => editMemory(id, content),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["memory-profile"] }),
  });
  // Forgets the bookmark only: the folder and its conversations stay where they are. Every spelling
  // of the folder goes, or the second one would reappear the moment the first was removed.
  const forgetProject = useMutation({
    mutationFn: async (paths: string[]) => {
      for (const path of paths) await forgetCodeProject(path);
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["code-projects"] }),
  });
  const saveIdentity = useMutation({
    mutationFn: putInstructions,
    onSuccess: (stored) => qc.setQueryData(["instructions"], stored),
  });
  const agentLanguage = identity.data?.language ?? "";
  const [languageDraft, setLanguageDraft] = useState(agentLanguage);
  useEffect(() => setLanguageDraft(agentLanguage), [agentLanguage]);

  if (profile.isLoading) return <Spinner />;
  if (profile.isError)
    return <ErrorState error={profile.error} onRetry={() => profile.refetch()} />;

  const persona = profile.data?.persona ?? [];
  const otherFactsExist = (layers.data?.total ?? 0) > 0;
  // The interface language appears only when it differs from the saved answer language: they are
  // separate choices, and the same value twice would read as two settings.
  const uiLanguage = LANGS.find((item) => item.code === lang)?.label ?? "";
  const listed = listedProjects(projects.data ?? []);

  return (
    <div className="space-y-4">
      <Panel title={t("settings.card.agent")}>
        {identity.isLoading ? (
          <Spinner />
        ) : identity.isError ? (
          <ErrorState error={identity.error} onRetry={() => identity.refetch()} />
        ) : (
          <>
            <div className="px-4 py-3">
              <label className="block text-sm font-medium" htmlFor="profile-agent-language">
                {t("settings.row.agentLanguage")}
              </label>
              <div className="mt-1 flex gap-2">
                <input
                  id="profile-agent-language"
                  className="field h-8 min-w-0 flex-1 px-2.5 text-sm"
                  placeholder={t("settings.placeholder.agentLanguage")}
                  value={languageDraft}
                  onChange={(event) => setLanguageDraft(event.target.value)}
                />
                <Button
                  size="sm"
                  disabled={!identity.data || saveIdentity.isPending || languageDraft === agentLanguage}
                  onClick={() =>
                    identity.data && saveIdentity.mutate({ ...identity.data, language: languageDraft })
                  }
                >
                  {t("common.save")}
                </Button>
              </div>
              {uiLanguage && uiLanguage !== agentLanguage ? (
                <p className="mt-1 text-xs text-muted-foreground">
                  {t("settings.row.language")}: {uiLanguage}
                </p>
              ) : null}
            </div>
            <ResponseStyleNote />
          </>
        )}
        <div className="px-4 py-3">
          <div className="text-sm font-medium">{t("profile.projects")}</div>
          {projects.isLoading ? (
            <p className="text-xs text-muted-foreground">{t("common.loading")}</p>
          ) : listed.length === 0 ? (
            <p className="text-xs text-muted-foreground">{t("profile.projects.empty")}</p>
          ) : (
            <ul className="divide-y divide-hairline text-sm">
              {listed.map(({ row, paths }) => {
                const label = projectLabel(row.path, { [row.path]: row.alias });
                const remove = t("profile.projects.remove");
                return (
                  <li key={row.folder_key || row.path} className="flex items-center gap-2 py-2">
                    <div className="min-w-0 flex-1">
                      <div className="truncate">{label}</div>
                      <div className="truncate font-mono text-xs text-muted-foreground" title={row.path}>
                        {row.path}
                      </div>
                    </div>
                    <Button
                      size="sm"
                      variant="ghost"
                      disabled={forgetProject.isPending}
                      aria-label={`${label} — ${remove}`}
                      onClick={() => forgetProject.mutate(paths)}
                    >
                      {remove}
                    </Button>
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      </Panel>
      <Panel
        title={t("profile.summary")}
        // The way to forget a fact is one click away rather than a tab the reader has to know about.
        action={
          onOpenMemory ? (
            <button type="button" onClick={onOpenMemory} className="text-xs text-accent-ink hover:underline">
              {t("profile.manage")}
            </button>
          ) : undefined
        }
      >
        {persona.length === 0 ? (
          <EmptyState text={otherFactsExist ? t("profile.noFacts") : t("profile.empty")} />
        ) : (
          <ul aria-label={t("profile.facts")} className="divide-y divide-hairline">
            {persona.map((item) => (
              <li key={item.id} className="px-4 py-2.5">
                <PersonaFactEditor
                  id={item.id}
                  content={item.content}
                  busy={editFact.isPending}
                  onSave={(content) => editFact.mutate({ id: item.id, content })}
                />
                {/* Where the fact came from. A persona fact the agent inferred and one you typed
                    yourself carry different weight, and you should be able to tell them apart. */}
                <div className="mt-1 flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground">
                  <span>{memorySourceLabel(t, item.source)}</span>
                  {/* The same word and colour Memory uses for it, instead of the raw "tainted". */}
                  {item.provenance === "tainted" ? <Badge tone="warn">{t("memory.unverified")}</Badge> : null}
                </div>
              </li>
            ))}
          </ul>
        )}
      </Panel>
    </div>
  );
}

function PersonaFactEditor({
  id,
  content,
  busy,
  onSave,
}: {
  id: string;
  content: string;
  busy: boolean;
  onSave: (content: string) => void;
}) {
  const t = useT();
  const [draft, setDraft] = useState(content);
  useEffect(() => setDraft(content), [content]);
  return (
    <div className="space-y-2">
      <textarea
        aria-label={`${t("profile.facts")} ${id}`}
        className="field min-h-12 w-full p-2 text-sm"
        value={draft}
        onChange={(event) => setDraft(event.target.value)}
      />
      <Button
        size="sm"
        disabled={busy || !draft.trim() || draft.trim() === content}
        onClick={() => onSave(draft.trim())}
      >
        {t("common.save")}
      </Button>
    </div>
  );
}
