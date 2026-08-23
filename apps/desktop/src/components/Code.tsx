import { useCallback, useEffect, useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import hljs from "highlight.js";
import {
  FileCode2,
  Folder,
  FolderGit2,
  Loader2,
  MessageSquare,
  Pencil,
  Save,
  X,
} from "lucide-react";
import {
  getConfig,
  getFsFile,
  getFsImage,
  saveFile,
  type Approval,
  type Profile,
  type Reach,
} from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/panel";
import { Agents } from "@/components/Agents";
import { Conversation } from "@/components/code/Conversation";
import { PostureNote } from "@/components/code/PostureNote";
import { ModelPicker } from "@/components/code/ModelPicker";
import { ProviderPicker } from "@/components/code/ProviderPicker";
import { Tooltip } from "@/components/ui/tooltip";
import { RolesBar } from "@/components/code/RolesBar";
import { SessionSidebar } from "@/components/code/SessionSidebar";
import { ProjectPicker } from "@/components/code/ProjectPicker";
import { useRunSession } from "@/lib/run-session";
import { useT } from "@/lib/i18n";
import { readWorkspace, writeWorkspace } from "@/lib/workspace";

const fieldCls = "field w-full px-3 text-sm";

/** Map a filename extension to a highlight.js language id; unknown → let hljs auto-detect. */
const EXT_LANG: Record<string, string> = {
  py: "python",
  ts: "typescript",
  tsx: "typescript",
  js: "javascript",
  jsx: "javascript",
  json: "json",
  md: "markdown",
  css: "css",
  scss: "scss",
  html: "xml",
  xml: "xml",
  yml: "yaml",
  yaml: "yaml",
  toml: "ini",
  ini: "ini",
  sh: "bash",
  bash: "bash",
  rs: "rust",
  go: "go",
  sql: "sql",
};

function highlightFile(content: string, name: string): string {
  try {
    const ext = name.includes(".") ? name.split(".").pop()!.toLowerCase() : "";
    const lang = EXT_LANG[ext];
    if (lang && hljs.getLanguage(lang)) return hljs.highlight(content, { language: lang }).value;
    return hljs.highlightAuto(content).value;
  } catch {
    // Never let a highlighter error blank the viewer — show the raw (escaped) text instead.
    const div = document.createElement("div");
    div.textContent = content;
    return div.innerHTML;
  }
}

/** Extensions the byte endpoint will serve, mirroring `_IMAGE_MEDIA_TYPES` in `chimera/api/fs_api.py`.
 *
 * Duplicated rather than fetched because this list decides only whether to ASK. The server decides
 * what it will hand back, and a stale copy here can at worst produce a request that comes back 415 —
 * never a file served on this list's say-so. Widening this without widening the server's changes
 * nothing about what is served, which is the property that makes the duplication safe.
 *
 * SVG is absent for the same reason it is absent server-side: it is script-capable under a top-level
 * navigation, and it needs nothing here anyway — an SVG is text, so the highlighted view already
 * shows it. */
const IMAGE_EXTS = new Set(["png", "jpg", "jpeg", "gif", "webp", "bmp"]);

function isImagePath(path: string): boolean {
  const ext = path.includes(".") ? path.split(".").pop()!.toLowerCase() : "";
  return IMAGE_EXTS.has(ext);
}

/** The chart the agent just drew, on the screen.
 *
 * `render_chart` and `generate_image` write into the workspace and the viewer answered "binary or
 * non-text" — our own app could not display the output of our own tools, in ten languages.
 *
 * The bytes arrive through `fetch` and become an object URL rather than going straight into
 * `<img src>`, because the request has to carry the bearer token in a header and an `<img>` cannot
 * send one. Putting the token in the query string instead would write it into every access log.
 *
 * The URL is revoked when the file changes or the panel unmounts: an object URL pins its blob in
 * memory until it is released, so a session spent clicking through screenshots would otherwise
 * accumulate every one of them. */
function ImagePreview({ workspace, path }: { workspace: string; path: string }) {
  const t = useT();
  const q = useQuery({
    queryKey: ["fs-image", workspace, path],
    queryFn: () => getFsImage(workspace || null, path),
  });
  const blob = q.data;
  const [url, setUrl] = useState("");

  useEffect(() => {
    if (!blob) {
      setUrl("");
      return;
    }
    const objectUrl = URL.createObjectURL(blob);
    setUrl(objectUrl);
    return () => URL.revokeObjectURL(objectUrl);
  }, [blob]);

  if (q.isError) {
    // Named separately from the generic binary note: "we could not load this image" is a different
    // fact from "this is not an image", and answering the second when the first happened would send
    // someone looking for a bug in a file that is fine.
    return <div className="px-4 py-6 text-sm text-bad">{t("code.imageError")}</div>;
  }
  if (!url) {
    return (
      <div className="flex justify-center py-10 text-muted-foreground">
        <Loader2 className="h-5 w-5 animate-spin" />
      </div>
    );
  }
  return (
    <div className="p-4">
      <img
        src={url}
        alt={t("code.imageAlt", { path })}
        className="max-w-full rounded-chip border border-hairline"
      />
    </div>
  );
}

/** The center column: a syntax-highlighted viewer with an opt-in editor. Read-only is the default;
 *  "Edit" swaps in a mono textarea → Save PUTs it (atomic + newline-preserving + size-capped
 *  server-side). Truncated/binary files are NOT editable (saving would clobber the unseen remainder). */
function Viewer({ workspace, path }: { workspace: string; path: string | null }) {
  const t = useT();
  const qc = useQueryClient();
  const q = useQuery({
    queryKey: ["fs-file", workspace, path],
    queryFn: () => getFsFile(workspace || null, path as string),
    enabled: path !== null,
  });
  const name = path ? path.split("/").pop() ?? path : "";
  const loaded = q.data?.content ?? "";
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [saving, setSaving] = useState(false);
  const [saveErr, setSaveErr] = useState(false);
  const [savedFlash, setSavedFlash] = useState(false);

  // Leave edit mode (and clear any flash) whenever the open file changes.
  useEffect(() => {
    setEditing(false);
    setSaveErr(false);
    setSavedFlash(false);
  }, [path]);

  const dirty = editing && draft !== loaded;
  // Only a clean, whole read is editable — a truncated (clipped at the read cap) or binary/non-text
  // file is not, since saving the shown text would overwrite the part we never loaded.
  const editable = path !== null && !!q.data && !q.data.note && !q.data.truncated;

  const html = useMemo(
    () => (q.data && q.data.content ? highlightFile(q.data.content, name) : ""),
    [q.data, name],
  );

  function startEdit() {
    setDraft(loaded);
    setSaveErr(false);
    setSavedFlash(false);
    setEditing(true);
  }
  function discard() {
    setDraft(loaded);
    setEditing(false);
    setSaveErr(false);
  }
  async function save() {
    if (!path || saving) return;
    setSaving(true);
    setSaveErr(false);
    try {
      await saveFile(workspace || null, path, draft);
      setEditing(false);
      setSavedFlash(true);
      // Re-read the file (its on-disk newline may differ from the draft) and refresh the tree.
      await qc.invalidateQueries({ queryKey: ["fs-file", workspace, path] });
      void qc.invalidateQueries({ queryKey: ["fs-tree"] });
    } catch {
      setSaveErr(true);
    } finally {
      setSaving(false);
    }
  }

  return (
    <section className="flex min-h-0 flex-1 flex-col border-hairline lg:border-r">
      <div className="flex items-center gap-2 border-b border-hairline px-4 py-2.5">
        <FileCode2 className="h-4 w-4 shrink-0 text-accent" />
        <span className="min-w-0 flex-1 truncate font-mono text-xs text-foreground">
          {path ?? t("code.noFile")}
        </span>
        {dirty ? <Badge tone="warn">{t("code.dirty")}</Badge> : null}
        {q.data?.truncated ? <Badge tone="warn">{t("code.truncated")}</Badge> : null}
        {savedFlash && !editing ? (
          <span className="text-xs text-ok">{t("code.saved")}</span>
        ) : null}
        {editing ? (
          <>
            <Button size="sm" variant="ghost" disabled={saving} onClick={discard}>
              <X className="h-3.5 w-3.5" /> {t("code.discard")}
            </Button>
            <Button size="sm" disabled={saving || !dirty} onClick={() => void save()}>
              {saving ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <Save className="h-3.5 w-3.5" />
              )}
              {t("code.save")}
            </Button>
          </>
        ) : editable ? (
          <Button size="sm" variant="ghost" onClick={startEdit}>
            <Pencil className="h-3.5 w-3.5" /> {t("code.edit")}
          </Button>
        ) : null}
      </div>
      {editing ? (
        <div className="flex min-h-0 flex-1 flex-col">
          <textarea
            className="min-h-0 flex-1 resize-none bg-transparent p-4 font-mono text-[12.5px] leading-relaxed text-foreground outline-none"
            value={draft}
            spellCheck={false}
            onChange={(e) => setDraft(e.target.value)}
          />
          <div className="border-t border-hairline px-4 py-1.5 text-xs">
            {saveErr ? (
              <span className="text-bad">{t("code.saveError")}</span>
            ) : (
              <span className="text-muted-foreground">{t("code.noUndo")}</span>
            )}
          </div>
        </div>
      ) : (
        <div className="min-h-0 flex-1 overflow-auto">
          {path === null ? (
            <div className="px-4 py-6 text-sm text-muted-foreground">{t("code.viewerHint")}</div>
          ) : q.isLoading ? (
            <div className="flex justify-center py-10 text-muted-foreground">
              <Loader2 className="h-5 w-5 animate-spin" />
            </div>
          ) : q.isError ? (
            <div className="px-4 py-6 text-sm text-bad">{t("code.fileError")}</div>
          ) : q.data?.note && path !== null && isImagePath(path) ? (
            <ImagePreview workspace={workspace} path={path} />
          ) : q.data?.note ? (
            // Still the honest answer for a .zip or a .wasm: there is no way to show those, and an
            // <img> pointed at one renders a broken-image icon, which claims a failure that did not
            // happen.
            <div className="px-4 py-6 text-sm text-muted-foreground">{t("code.binaryNote")}</div>
          ) : (
            <pre className="overflow-x-auto p-4 text-[12.5px] leading-relaxed">
              <code className="hljs bg-transparent" dangerouslySetInnerHTML={{ __html: html }} />
            </pre>
          )}
        </div>
      )}
    </section>
  );
}

export function Code() {
  const t = useT();
  const qc = useQueryClient();
  // Lazy initialiser, not `useState(readWorkspace())`: the latter reads storage on every render.
  const [workspace, setWorkspace] = useState(readWorkspace);
  const [openFile, setOpenFile] = useState<string | null>(null);
  const [projectDraft, setProjectDraft] = useState(readWorkspace);
  // Which stored conversation is on screen, and a key that remounts the transcript when it
  // changes — the conversation holds its exchanges in state, so switching sessions has to
  // discard them rather than let the previous project's turns sit above the new one.
  const [picking, setPicking] = useState(false);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [conversationKey, setConversationKey] = useState(0);
  // The conversation and the run share one workspace, so they share two facts: what the user asked
  // (handed over by "Run with verification") and whether a run is already in flight.
  // A confirmed decomposition. Keyed by `at` so a second batch is a new board rather than a restart
  // of the old one — the board starts its runs on mount, which is only correct if mounting is what a
  // new batch does.
  const [batch, setBatch] = useState<{ tasks: string[]; at: number } | null>(null);
  // Read from the shared session rather than from the panel below, which is about to be deleted.
  // This is strictly WIDER than what it replaces: the session also sees runs launched from the Work
  // screen, which the local flag never did — so the conversation now refuses to send while ANY run
  // is writing in this workspace, not just one started here.
  const run = useRunSession();
  // Only when the run is in THIS project. A run elsewhere cannot race this workspace, and
  // blocking on it would be a lie about why. A run with no workspace still blocks: not
  // knowing which directory it is editing is a reason to be careful, not a reason to allow.
  const runBusy = run.running && (run.workspace === null || run.workspace === workspace);
  // Not chosen here, and not chosen before typing either: what the agent may do is a standing
  // decision, so it lives in Settings and this screen reads it. The fallbacks are the pair this was
  // hardcoded to — edit the workspace, no shell, stop and ask if the run read something untrusted —
  // so an install that has configured nothing behaves exactly as it did.
  //
  // Still SENT on every request rather than omitted: omitting resolves to no tool denials and no
  // pause at all, which is more permissive than any corner someone could have picked. The server
  // applies the configured posture as a floor regardless; sending it keeps the two in agreement, so
  // the posture line in the transcript describes the run that is actually happening.
  const cfg = useQuery({ queryKey: ["config"], queryFn: getConfig });
  const reach = (cfg.data?.autonomy.reach || "workspace") as Reach;
  const approval = (cfg.data?.autonomy.approval || "suspicious") as Approval;
  const posture = useMemo(() => ({ reach, approval }), [reach, approval]);
  // Who does the work. Session-local rather than persisted: handing your workspace to another
  // company's agent is a decision per piece of work, not a setting that quietly stays on from the
  // last time — and a persisted default here would be the app choosing on the user's behalf.
  const [provider, setProvider] = useState("");
  // Which model answers, for this conversation. Session-local for the same reason as `provider` and
  // the spend ceiling: a model quietly carried over from a week ago is how a turn ends up costing
  // thirty times what the person expected. "" means the install's default, and the picker offers to
  // make a pick the standing default — which is the same intent, stated rather than accumulated.
  const [model, setModel] = useState("");
  // Which model does which job. Session-local, like `provider` and `model` above and for the same
  // reason: routing that quietly carried over from last week is how a turn costs what nobody
  // expected.
  //
  // It was `const profile = "balanced"` with a comment admitting there was no picker on any screen
  // — and `RolesBar`, which IS that picker and carries a `compact` mode documented as "for the
  // composer strip", existed with exactly one occurrence in the whole source: its own definition.
  // A control written for a place it was never put, so "economy" and "max" were unreachable words.
  //
  // A run started below still reports `profile_source: "system"` when nobody touched this, so the
  // cost panel keeps counting a default apart from a profile somebody deliberately chose.
  const [profile, setProfile] = useState<Profile>("balanced");
  const [profileTouched, setProfileTouched] = useState(false);

  /** Change the project this screen is working in. One function, because it was three near-copies.
   *
   * Clearing `sessionId` is the part all three were missing, and it is the part that matters: the
   * server fixes a conversation's project when the conversation is created and never moves it, so
   * carrying the id across a project change left the next turn writing into a conversation filed
   * under the OLD project. The screen said one thing and the disk said another, and the disk was
   * right. Invisible with one workspace; routine with several.
   *
   * Resuming a conversation is deliberately NOT this: it also changes the project, but it is
   * arriving at an existing conversation rather than leaving one.
   */
  const switchProject = useCallback(
    (next: string) => {
      if (next === workspace) return;
      setWorkspace(next);
      writeWorkspace(next);
      setProjectDraft(next);
      setSessionId(null);
      startConversation();
      void qc.invalidateQueries({ queryKey: ["fs-file"] });
      void qc.invalidateQueries({ queryKey: ["git-status"] });
    },
    [qc, workspace],
  );

  /** Everything that has to be true at the START of a conversation.
   *
   *  The model pick says "Applies to this conversation" and is described in code as session-local,
   *  "because a model quietly carried over from a week ago is how a turn ends up costing thirty
   *  times what the person expected". It was screen state: it survived New conversation, Resume and
   *  a project switch, so the chip said one scope and the state had another. The fusion cast was
   *  already correct — remounted by `key={conversationKey}` — which is what made the two disagree.
   *
   *  One function because it was three near-copies, and the copy that forgot a line is exactly how
   *  this happened.
   */
  const startConversation = useCallback(() => {
    setConversationKey((n) => n + 1);
    setOpenFile(null);
    setModel("");
  }, []);

  const refreshOpenFile = useCallback(() => {
    if (openFile) void qc.invalidateQueries({ queryKey: ["fs-file", workspace, openFile] });
  }, [qc, workspace, openFile]);

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex items-center gap-2.5 border-b border-hairline px-5 py-3 text-accent">
        <FileCode2 className="h-5 w-5" />
        <h1 className="text-sm font-semibold text-foreground">{t("code.title")}</h1>
      </div>
      {/* Which project, on one line, the way a coding tool names the repo it is pointed at. What
          used to be here was a file TREE: a quarter of the window asking the user to navigate to
          the file they wanted changed. That is the job the agent is for — it greps, it reads, it
          finds. A browser in front of an agent that can search is the user doing the agent's work,
          and it also framed the screen as "maintain THIS folder" rather than "get MY work done".
          Files still open — from the transcript, by clicking a path the agent actually touched. */}
      <form
        className="flex items-center gap-2 border-b border-hairline px-5 py-2"
        onSubmit={(e) => {
          e.preventDefault();
          switchProject(projectDraft.trim());
        }}
      >
        <FolderGit2 className="h-4 w-4 shrink-0 text-accent" />
        <input
          className={`${fieldCls} h-7 max-w-xl font-mono text-xs`}
          placeholder={t("code.workspacePlaceholder")}
          value={projectDraft}
          onChange={(e) => setProjectDraft(e.target.value)}
        />
        <Button size="sm" type="submit" variant="ghost">
          {t("code.open")}
        </Button>
        {/* The typed field stays: someone who knows the path should not have to click through to
            it, and it is what the tests and a paste from a terminal both use. The picker is for
            everyone else.

            `type="button"` is load-bearing, not tidiness. A `<button>` inside a `<form>` submits it
            by default, so this one ran `switchProject(projectDraft)` as well as opening the picker
            — and the natural way to reach for it is with an EMPTY field, because not wanting to
            type is the reason you clicked. That switched the root to nothing and started a new
            conversation before showing a single folder to choose from. Choosing is a navigation
            gesture; it must not decide anything. */}
        <Button size="sm" type="button" variant="ghost" onClick={() => setPicking((p) => !p)}>
          <Folder className="h-4 w-4" /> {t("code.picker.browse")}
        </Button>
        {/* The way out of a project, which did not exist.
            A conversation with no workspace works — the turn runs, answers, is priced and reads
            memory — and the sidebar already groups those under their own heading. What was missing
            was any way to START one: once a folder was chosen, every new conversation inherited it,
            and nothing on the screen let go. So the plain "ask it something" conversation was
            reachable only by never having opened a project in the first place.
            Only when there IS one to leave, and `switchProject("")` already does exactly the right
            thing — clears the root, drops the session id, opens a new conversation. */}
        {workspace ? (
          <Tooltip label={t("code.noProject.hint")}>
            <Button size="sm" type="button" variant="ghost" onClick={() => switchProject("")}>
              <MessageSquare className="h-4 w-4" /> {t("code.noProject")}
            </Button>
          </Tooltip>
        ) : null}
      </form>
      {picking ? (
        <div className="border-b border-hairline px-5 py-2">
          <ProjectPicker
            onCancel={() => setPicking(false)}
            onPick={(path) => {
              setPicking(false);
              switchProject(path);
            }}
          />
        </div>
      ) : null}
      <div className="flex min-h-0 flex-1 flex-col lg:flex-row">
        <SessionSidebar
          workspace={workspace}
          activeSession={sessionId}
          onNew={() => {
            // A new conversation, not a cleared one: the old transcript stays on disk and stays in
            // the list. Clearing used to be the only way to start over, and it deleted the session.
            setSessionId(null);
            startConversation();
          }}
          onResume={(session) => {
            setSessionId(session.id);
            startConversation();
            if (session.workspace !== workspace) {
              setWorkspace(session.workspace);
              writeWorkspace(session.workspace);
              setProjectDraft(session.workspace);
            }
          }}
          onProject={switchProject}
        />
        {/* The conversation IS the screen. It used to be one of five panels in a 384px column, and
            the arithmetic did not work: the panels that could not shrink took every pixel and this
            was laid out at zero height. Git and the cost table now live on Work, where reviewing
            what a run did belongs; the two settings became one row inside the composer. */}
        {/* `min-w-0` on the column that is MEANT to absorb the shrinking.
            rc16 put it on the inner Conversation div and on the viewer, and missed this one — the
            row's own flex-1 child. So at 1280px the conversation held 778px of a 936px row, the
            viewer was crushed to 0.67px, and the pair overflowed 82px into the activity panel.
            Measured by hit-testing a grid inside the panel: the composer strip and the transcript
            bubbles were painting there, not the code this time. */}
        <main className="flex min-h-0 min-w-0 flex-1 flex-col">
          <Conversation
            key={conversationKey}
            resumeSession={sessionId}
            workspace={workspace}
            openFile={openFile}
            onOpenFile={setOpenFile}
            onHandOff={(text) =>
              // Straight into the shared run — the multi-attempt, revert-if-it-fails path the button
              // actually promises. It used to fill a form below and wait, so the user could set the
              // verify command and the attempt count first; those fields no longer exist, and the
              // server infers the verify command from the project. Waiting for someone to press a
              // second button on a form with nothing left to fill in is not consent, it is friction.
              // The global status bar shows it and can stop it from any screen.
              run.start({
                task: text,
                verify: null,
                workspace: workspace || null,
                max_attempts: 3,
                // The bar above IS the picker now, and this dropped what it chose: a run started
                // here took the built-in tiers whatever the bar said, and then filed the receipt
                // under "system". `worth.py` groups by (profile, profile_source) precisely so a run
                // somebody chose "max" for and a run that got the default stay separate evidence —
                // which only works if what was chosen actually travels.
                profile,
                profile_source: profileTouched ? "user" : "system",
              })
            }
            onBatch={(tasks) => setBatch({ tasks, at: Date.now() })}
            onEdited={refreshOpenFile}
            busyElsewhere={runBusy}
            posture={posture}
            provider={provider}
            model={model}
            profile={profile}
            /* No selectors. What they expressed is now a server default the app SENDS (never omits
               — an absent posture means no tool denials and no pause at all, which is more permissive
               than any corner of the grid a user could have chosen) and a line of evidence in the
               transcript once it becomes relevant. */
            controls={
              <div className="flex flex-col gap-1.5">
                {/* The picker sits ABOVE the sentence, because the sentence is about the choice.
                    Reversed, someone reads what the agent may do to their files and then changes
                    who the agent is — which is the order in which a promise stops being true. */}
                <ProviderPicker value={provider} onChange={setProvider} disabled={runBusy} />
                {/* Only for Chimera's own loop, same as the model picker below: an external agent
                    routes its own roles, and offering the control beside it would describe a
                    decision this app does not get to make. */}
                {provider === "" ? (
                  <RolesBar
                    compact
                    profile={profile}
                    onProfile={(p) => {
                      setProfile(p);
                      setProfileTouched(true);
                    }}
                    disabled={runBusy}
                  />
                ) : null}
                {/* Only for Chimera's own loop. Claude Code and Gemini pick their own model, so a
                    selector next to them would offer a choice this app cannot make — and the turn
                    would run on something other than what the row says. */}
                {provider === "" ? (
                  <ModelPicker value={model} onChange={setModel} disabled={runBusy} />
                ) : null}
                <PostureNote
                  workspace={workspace}
                  reach={reach}
                  approval={approval}
                  provider={provider}
                />
              </div>
            }
          />
          {/* Several agents at once, once someone said yes to running several agents at once. This
              was a destination — a tab you picked before knowing whether the work was parallel, with
              its own launcher asking for tasks, a model, a worker count and a fusion mode. It is a
              consequence now, and the only question it still asks was asked before the worktrees
              existed rather than reported after they did. */}
          {batch ? (
            <div className="min-h-0 shrink-0 border-t border-hairline">
              <Agents
                key={batch.at}
                workspace={workspace}
                tasks={batch.tasks}
                posture={posture}
                profile={profile}
              />
            </div>
          ) : null}
        </main>
        {/* The viewer is a consequence of opening a file, not a permanent third of the window. */}
        {/* `min-w-0` for the same reason as the conversation column beside it: a fixed
            `lg:w-[28rem]` is a BASIS, not a ceiling, and a flex child without it will not shrink —
            it overflowed the row instead and painted across the activity panel. */}
        {openFile ? (
          <div className="flex min-h-0 min-w-0 shrink-0 flex-col border-hairline lg:w-[28rem] lg:border-l">
            <Viewer workspace={workspace} path={openFile} />
          </div>
        ) : null}
      </div>
    </div>
  );
}
