import type {
  AgentDef,
  AgentIdentity,
  AgentsBatch,
  AppConfig,
  DelegationSummary,
  HierarchyPreview,
  ConfigTest,
  CronJob,
  DoctorInfo,
  FsFile,
  FsFileWritten,
  FsTree,
  GitCommitResult,
  GitDiff,
  GitInitResult,
  GitRevertResult,
  GitStatus,
  RouteMeta,
  Resources,
  CompletionAcceptance,
  DiagnosticsResult,
  InlineCompletion,
  SearchResult,
  Benchmarks,
  GovernanceAudit,
  InjectionReport,
  CatalogEntry,
  LibraryCard,
  SkillBundle,
  CodeSessionRaw,
  Maturity,
  McpServers,
  McpTest,
  MemoryItem,
  MemoryLayers,
  MemoryProfile,
  ModelListing,
  OllamaModels,
  PoolWrite,
  ProjectState,
  RunReceipt,
  SkillStat,
  TaskCard,
  Tools,
  UsageSummary,
  VersionInfo,
} from "@/lib/types";
import { apiUrl, token } from "@/lib/server";

// Where the request goes and which token it carries both come from `server.ts`: the local sidecar
// keeps the shipped behaviour exactly (relative path, token from the meta tag the backend injects
// for a loopback client), and a remote server supplies its own base URL and its own token, because
// a remotely-served page is never given one.
//
// Read per call, never captured at module load: the active server can change while the window is
// open, and a constant here would keep sending to the previous one until relaunch.
function authHeaders(extra?: HeadersInit): HeadersInit {
  const base: Record<string, string> = { "Content-Type": "application/json" };
  const bearer = token();
  if (bearer) base.Authorization = `Bearer ${bearer}`;
  return { ...base, ...(extra ?? {}) };
}

/** Auth WITHOUT a `Content-Type` — the header a `FormData` body must not be given.
 *
 *  Only the browser knows the multipart boundary it just generated, and it writes the header for you
 *  precisely when you have not written one yourself. Sending `Content-Type: application/json` next to
 *  a FormData body — which is what `authHeaders()` does — does not change the body: the bytes are
 *  still multipart, they just arrive labelled as JSON with no boundary to parse them by. The server
 *  then answers `422 field required` about a file that was in the request all along, which reads to
 *  the user as "the upload is broken" and to a developer as "the endpoint is wrong". */
function authHeadersNoContentType(): HeadersInit {
  const bearer = token();
  return bearer ? { Authorization: `Bearer ${bearer}` } : {};
}

/** The reason a request was refused, when the server gave one.
 *
 *  Every refusal in this app used to reach the screen as "400 Bad Request" — the status line,
 *  never the sentence. So a backend that answers `workspace not found: C:\...` with the path in
 *  it, precisely so a person can see WHICH folder was missing, was answering into a wall.
 *
 *  FastAPI's `detail` is a string for a raised HTTPException and a list of objects for a schema
 *  rejection; only the first is a sentence written for a human, so only the first is used. */
async function refusal(res: Response): Promise<string> {
  const fallback = `${res.status} ${res.statusText}`;
  try {
    const body = (await res.json()) as { detail?: unknown };
    const detail = body?.detail;
    if (typeof detail === "string" && detail.trim()) return detail.slice(0, 400);
  } catch {
    // Not JSON at all — a proxy page, an empty body, a connection cut mid-response. The status
    // line is all there is, and it is better than an exception thrown while reporting one.
  }
  return fallback;
}

/** The same, for the streaming surfaces, which also fail when a 200 arrives with no body. */
async function streamRefusal(res: Response): Promise<string> {
  if (!res.ok) return refusal(res);
  return "the server accepted the request and sent nothing";
}

async function json<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(apiUrl(path), { ...init, headers: authHeaders(init?.headers) });
  if (!res.ok) throw new Error(await refusal(res));
  return (await res.json()) as T;
}


// The running version + an HONEST update signal: `update_available` is true ONLY when GitHub confirms
// a strictly-newer release. Offline / any error → {latest:null, update_available:false} (never a false
// "update available"). The backend caches the GitHub result, so polling this is cheap.
export const getVersion = () => json<VersionInfo>("/api/version");

export const getConfig = () => json<AppConfig>("/api/config");
export const getInstructions = () => json<AgentIdentity>("/api/instructions");
// The agents you send work to. Every call returns the WHOLE registry, so a screen never has
// to guess what the list looks like after a change it just made.
export const getAgentRegistry = () => json<AgentDef[]>("/api/agents/registry");
export const putAgent = (agent: AgentDef) =>
  json<AgentDef[]>("/api/agents/registry", { method: "PUT", body: JSON.stringify(agent) });
export const deleteAgent = (id: string) =>
  json<AgentDef[]>(`/api/agents/registry/${encodeURIComponent(id)}`, { method: "DELETE" });
export const getDoctor = () => json<DoctorInfo>("/api/doctor");
/** The Ollama tags this machine has pulled. NOT part of `doctor`: that response is fetched by
 *  several screens, and a round-trip to a server that may be off would make all of them wait. */
export const getOllamaModels = () => json<OllamaModels>("/api/models/ollama");
/** The models a turn may name — the curated catalogue merged with OpenRouter's live index and
 *  whatever Ollama has pulled, filtered to the keys this install actually has.
 *
 *  `provider` forces one remote catalogue to be listed regardless of the keys present. Onboarding
 *  passes it, because there the user is holding a key that has not been saved yet and "what does
 *  this buy" is exactly the question. Everywhere else, omit it. */
export const getModels = (provider?: string) =>
  json<ModelListing>(`/api/models${provider ? `?provider=${encodeURIComponent(provider)}` : ""}`);
export const getUsage = () => json<UsageSummary>("/api/usage");
// `workspace` narrows to one project; omitted returns every project's runs, which is what a
// caller with no project selected means and what this endpoint has always done.
export const getRuns = (workspace?: string) =>
  json<RunReceipt[]>(`/api/runs${workspace ? `?workspace=${encodeURIComponent(workspace)}` : ""}`);
export const getGovernanceInjection = () =>
  json<InjectionReport>("/api/governance/injection");
export const getGovernanceAudit = () => json<GovernanceAudit>("/api/governance/audit");
export const getTools = () => json<Tools>("/api/tools");

// --- Filesystem (read-only tree + file viewer for the Code screen) ---
// Both are path-scoped server-side to the workspace; a `..` escape is a 400, a binary/dir is an
// honest note (never a 500). The tree is lazy — one directory level per call.
export const getFsTree = (workspace?: string | null, path = "") => {
  const params = new URLSearchParams({ path });
  if (workspace) params.set("workspace", workspace);
  return json<FsTree>(`/api/fs/tree?${params.toString()}`);
};
export const getFsFile = (workspace: string | null | undefined, path: string) => {
  const params = new URLSearchParams({ path });
  if (workspace) params.set("workspace", workspace);
  return json<FsFile>(`/api/fs/file?${params.toString()}`);
};
// Editable-viewer save (PUT): atomic + newline-preserving + size-capped server-side. A `..` escape or
// oversize content is a 400. Returns the bytes actually written (may exceed content length on a CRLF file).
export const saveFile = (workspace: string | null | undefined, path: string, content: string) =>
  json<FsFileWritten>("/api/fs/file", {
    method: "PUT",
    body: JSON.stringify({ workspace: workspace || null, path, content }),
  });
/** The raw bytes of an image in the workspace, as a Blob to point an `<img>` at.
 *
 * Not `json()`, and not an `<img src="/api/fs/image?…">` either. The token travels in a header, and
 * an `<img>` element cannot send one — the only way to put it in the URL instead would be a query
 * parameter, which writes the bearer token into every access log and every browser history between
 * here and nowhere. So the bytes are fetched like any other guarded call and handed to the element
 * as an object URL, which the caller must revoke.
 *
 * The server serves `image/*` and only `image/*` (allowlist + `nosniff`), so what comes back cannot
 * be a document that reads the token back out of this origin.
 */
export async function getFsImage(
  workspace: string | null | undefined,
  path: string,
): Promise<Blob> {
  const params = new URLSearchParams({ path });
  if (workspace) params.set("workspace", workspace);
  // Auth without a `Content-Type`: a GET has no body to describe, and the response type is the
  // server's to state — this request must not appear to be asking for JSON.
  const res = await fetch(apiUrl(`/api/fs/image?${params.toString()}`), {
    headers: authHeadersNoContentType(),
  });
  // Same reason as `json`: an image that could not be read has a server-side reason, and "400
  // Bad Request" is not it.
  if (!res.ok) throw new Error(await refusal(res));
  return res.blob();
}

// --- Cross-file search ---
// A POST, not a GET with `?q=`: the query is the user's own text, and a URL is written to every
// access log between here and nowhere. Someone searching their repository for a token they are
// removing should not have it logged on the way. Scoped to `workspace` like the tree beside it.
export const searchFiles = (
  workspace: string | null | undefined,
  query: string,
  options: { regex?: boolean; caseSensitive?: boolean; glob?: string } = {},
) =>
  json<SearchResult>("/api/fs/search", {
    method: "POST",
    body: JSON.stringify({
      query,
      workspace: workspace || null,
      regex: options.regex ?? false,
      case_sensitive: options.caseSensitive ?? false,
      glob: options.glob ?? "",
    }),
  });

// --- Diagnostics from a language server ---
// The BUFFER travels, not just the path: the editor's text is what the person is looking at, and
// diagnosing the saved copy would put every squiggle one save behind — pointing at problems they
// already fixed. `available: false` is not an empty list of problems; it means nothing looked.
export const getDiagnostics = (
  workspace: string | null | undefined,
  path: string,
  text: string,
) =>
  json<DiagnosticsResult>("/api/lsp/diagnostics", {
    method: "POST",
    body: JSON.stringify({ path, text, workspace: workspace || null }),
  });

// --- Inline completion ---
// `signal` is not optional decoration. Every keystroke starts a request, and a request nobody
// aborted keeps a local GPU busy producing text for a cursor that has moved — which looks exactly
// like a slow model from the outside. The server has its own single-flight for the same reason;
// these are two halves of one guarantee, not a belt and braces.
export const getInlineCompletion = (
  prefix: string,
  suffix: string,
  key: string,
  signal?: AbortSignal,
) =>
  json<InlineCompletion>("/api/complete/inline", {
    method: "POST",
    body: JSON.stringify({ prefix, suffix, key }),
    signal,
  });

// Tab or Escape. Fire-and-forget: an unrecorded outcome costs a sample, and blocking the editor on
// a statistic would be the wrong trade.
export const postCompletionOutcome = (id: string, accepted: boolean) =>
  json<CompletionAcceptance>("/api/complete/outcome", {
    method: "POST",
    body: JSON.stringify({ id, accepted }),
  });

export const getCompletionStats = () => json<CompletionAcceptance>("/api/complete/stats");

// --- What this machine is spending ---
// Every field is nullable and that is the contract, not an oversight: a measurement that could not
// be taken is absent, never zero. 0% VRAM on an AMD card would be believed, and would be wrong
// about hardware the user is looking at.
export const getResources = () => json<Resources>("/api/resources");

// --- Git (status / diff / commit / scoped revert for the Code screen's git panel) ---
// All gate on `is_git_repo` server-side: a non-repo (or git-missing) folder returns the honest
// {is_repo: false} empty-state, never a 500. Commit stages EXPLICIT paths (never `add -A`); revert is
// git-backed and scoped to the passed paths only (never workspace-wide).
export const getGitStatus = (workspace?: string | null) => {
  const params = new URLSearchParams();
  if (workspace) params.set("workspace", workspace);
  const qs = params.toString();
  return json<GitStatus>(`/api/git/status${qs ? `?${qs}` : ""}`);
};
export const getGitDiff = (workspace: string | null | undefined, path?: string | null, staged = false) => {
  const params = new URLSearchParams();
  if (workspace) params.set("workspace", workspace);
  if (path) params.set("path", path);
  if (staged) params.set("staged", "true");
  const qs = params.toString();
  return json<GitDiff>(`/api/git/diff${qs ? `?${qs}` : ""}`);
};
export const gitCommit = (workspace: string | null | undefined, message: string, paths: string[]) =>
  json<GitCommitResult>("/api/git/commit", {
    method: "POST",
    body: JSON.stringify({ workspace: workspace || null, message, paths }),
  });
export const gitRevert = (workspace: string | null | undefined, paths: string[]) =>
  json<GitRevertResult>("/api/git/revert", {
    method: "POST",
    body: JSON.stringify({ workspace: workspace || null, paths }),
  });
/** `git init` in the folder, plus a commit of what is already there.
 *
 * The commit is the reason this is one call rather than two. `git init` alone leaves a repo with no
 * HEAD: the isolation the batch runner wants exists, but there is nothing to go back TO — and the
 * moment after this returns is the moment the agent gets write and shell access to the folder.
 *
 * An already-initialised folder answers `{ok: false, error}` rather than committing over the user's
 * work, so a double-click is safe.
 */
export const gitInit = (workspace: string | null | undefined) =>
  json<GitInitResult>("/api/git/init", {
    method: "POST",
    body: JSON.stringify({ workspace: workspace || null }),
  });

export const getMaturity = () => json<Maturity>("/api/maturity");
// The agent's REAL recorded benchmark numbers (the promising weak-model lift + the humbling external
// Terminal-Bench), each carrying its n/CI/significance. Read-only from the shipped snapshot; an
// unavailable snapshot returns {available:false}, never a 500.
export const getBenchmarks = () => json<Benchmarks>("/api/benchmarks");
export const patchConfig = (updates: Record<string, string>) =>
  json<{ updated: string[] }>("/api/config", { method: "PATCH", body: JSON.stringify(updates) });

/** Add one key to a provider's rotation pool. ONE key, never the list.
 *
 *  The list stays on the server, which is what makes a masked display safe: this client has only
 *  ever seen `…abcd` for the other entries, and there is no request it could make that would send
 *  them back. A "save the whole pool" endpoint would have needed the real values here. */
export const addPoolKey = (provider: string, key: string) =>
  json<PoolWrite>(`/api/config/pool/${provider}`, {
    method: "POST",
    body: JSON.stringify({ key }),
  });

/** Remove the key at `index`. A position, never a value — see {@link addPoolKey}. */
export const removePoolKey = (provider: string, index: number) =>
  json<PoolWrite>(`/api/config/pool/${provider}/${index}`, { method: "DELETE" });
// Returns the STORED identity, not the submitted one: the free text is capped server-side, so the
// screen must show what the agent will actually be told rather than what was typed into it.
export const putInstructions = (identity: AgentIdentity) =>
  json<AgentIdentity>("/api/instructions", {
    method: "PUT",
    body: JSON.stringify(identity),
  });
// The ONLY honest "key works" call: makes a real 1-token completion server-side. `ok:true` means it
// authenticated; otherwise `error` carries a short, secret-free message. Used by the onboarding wizard.
export const testProviderKey = (model?: string) =>
  json<ConfigTest>("/api/config/test", {
    method: "POST",
    body: JSON.stringify({ model: model ?? null }),
  });

// --- Memory ---
export const getMemory = (q = "") =>
  json<MemoryItem[]>(`/api/memory${q ? `?q=${encodeURIComponent(q)}` : ""}`);
export const getMemoryLayers = () => json<MemoryLayers>("/api/memory/layers");
// The agent's learned picture of you. The UI could WRITE persona facts (addMemory with
// kind: "persona") but had no way to read them back — you fed a store you could never inspect.
export const getMemoryProfile = () => json<MemoryProfile>("/api/memory/profile");
export const addMemory = (content: string, kind: string) =>
  json<{ status: string; item: MemoryItem }>("/api/memory", {
    method: "POST",
    body: JSON.stringify({ content, kind }),
  });
export const deleteMemory = (id: string) =>
  json<{ deleted: boolean }>(`/api/memory/${id}`, { method: "DELETE" });

// --- Skills ---
export const getSkills = () =>
  json<{ stats: SkillStat[]; retirement_candidates: string[] }>("/api/skills");
export const approveSkill = (name: string) =>
  json<{ approved: boolean }>(`/api/skills/${name}/approve`, { method: "POST" });
export const retireSkill = (name: string) =>
  json<{ retired: boolean }>(`/api/skills/${name}/retire`, { method: "POST" });

/** The curated skill cards that ship with Chimera — distinct from the learned ones above.
 *
 * Those are distilled from the user's own verified runs, so on a fresh install there are none and
 * the Skills screen was empty for everybody on day one. These twenty-three ship in the box and had
 * no route at all: the only documented way to use one was a CLI command naming a repo-relative
 * path, which resolves only inside a git checkout. */
export const getSkillLibrary = () => json<LibraryCard[]>("/api/skills/library");

// --- Installable skills ------------------------------------------------------------------------
// A different SHAPE of skill from the cards above, not a longer list of the same one: these are
// directories that ship scripts, fetched from their author's repository on request. Nothing is
// bundled with the app, and nothing installed is switched on by itself.

export const getSkillCatalog = () => json<CatalogEntry[]>("/api/skills/catalog");
export const getSkillBundles = () => json<SkillBundle[]>("/api/skills/bundles");
export const installSkillBundle = (name: string) =>
  json<SkillBundle>(`/api/skills/catalog/${encodeURIComponent(name)}/install`, { method: "POST" });
export const setSkillBundleStatus = (name: string, status: "active" | "inactive") =>
  json<SkillBundle>(`/api/skills/bundles/${encodeURIComponent(name)}/status`, {
    method: "POST",
    body: JSON.stringify({ status }),
  });
export const uninstallSkillBundle = (name: string) =>
  json<{ retired: boolean }>(`/api/skills/bundles/${encodeURIComponent(name)}`, {
    method: "DELETE",
  });
/** One card WITH its body. The list carries metadata only — twenty-three Trigger/Do/Avoid/Check/Risk
 *  bodies is a quarter of a megabyte to draw a list of names. */
export const getSkillLibraryCard = (name: string) =>
  json<LibraryCard>(`/api/skills/library/${encodeURIComponent(name)}`);
export const importSkillLibraryCard = (name: string) =>
  json<{ imported: boolean; name: string; status: string }>(
    `/api/skills/library/${encodeURIComponent(name)}/import`,
    { method: "POST" },
  );

// --- Messaging (reach the user on Discord/Telegram) ---
export type MessagingPlatform = { configured: boolean; running: boolean; error: string | null };
export type MessagingStatus = Record<string, MessagingPlatform>;
export const getMessaging = () => json<MessagingStatus>("/api/messaging");
export const startMessaging = (platform: string) =>
  json<MessagingStatus>(`/api/messaging/${platform}/start`, { method: "POST" });
export const stopMessaging = (platform: string) =>
  json<MessagingStatus>(`/api/messaging/${platform}/stop`, { method: "POST" });

// --- Cron ---
export const getCron = () => json<CronJob[]>("/api/cron");
export const createCron = (body: { name: string; schedule: string; action: string }) =>
  json<CronJob>("/api/cron", { method: "POST", body: JSON.stringify(body) });
export const enableCron = (id: string) => json<CronJob>(`/api/cron/${id}/enable`, { method: "POST" });
export const disableCron = (id: string) =>
  json<CronJob>(`/api/cron/${id}/disable`, { method: "POST" });
export const deleteCron = (id: string) =>
  json<{ deleted: boolean }>(`/api/cron/${id}`, { method: "DELETE" });

// --- MCP / Integrations ---
// Config reads/writes are cheap and NEVER connect (env values are never returned). `testMcpServer` is
// the ONLY connecting call — a real stdio connect + tool enumeration, the sole honest "connected" proof.
export const getMcpServers = () => json<McpServers>("/api/mcp");
export const addMcpServer = (body: {
  name: string;
  command: string;
  args: string[];
  env: Record<string, string>;
}) => json<McpServers>("/api/mcp", { method: "POST", body: JSON.stringify(body) });
export const removeMcpServer = (name: string) =>
  json<{ deleted: boolean }>(`/api/mcp/${encodeURIComponent(name)}`, { method: "DELETE" });
export const testMcpServer = (name: string) =>
  json<McpTest>(`/api/mcp/${encodeURIComponent(name)}/test`, { method: "POST" });

// --- Tasks (kanban + projects, HITL) ---
export const getKanban = () => json<Record<string, TaskCard[]>>("/api/kanban");
export const addKanbanCard = (card: {
  title: string;
  action?: string;
  lane?: string;
  verify?: string | null;
}) => json<TaskCard>("/api/kanban/cards", { method: "POST", body: JSON.stringify(card) });
export const moveKanbanCard = (id: string, column: string) =>
  json<TaskCard>(`/api/kanban/cards/${encodeURIComponent(id)}`, {
    method: "PATCH",
    body: JSON.stringify({ column }),
  });
export const removeKanbanCard = (id: string) =>
  json<{ deleted: boolean }>(`/api/kanban/cards/${encodeURIComponent(id)}`, { method: "DELETE" });

/** One card, worked. Hand-typed like every other stream here: SSE frames are not response models,
 *  so they never reach the generated OpenAPI types. */
export interface DispatchOutcome {
  card_id: string;
  lane: string;
  success: boolean;
  moved_to: string;
}

/** Dispatch the backlog, reporting each card as it lands rather than once at the end.
 *
 * The read loop is the fourth copy of the same twenty lines in this file. Extracting a shared reader
 * is the obvious cleanup and is deliberately NOT done here: it would touch three working streams,
 * and a refactor that arrives with a feature is a refactor nobody can review separately from it.
 */
export async function streamKanbanRun(
  req: {
    limit?: number | null;
    workspace?: string | null;
    model?: string | null;
    /** Cards worked at once. Above 1 each gets its own git worktree; see the backend's dispatch. */
    workers?: number;
  },
  handlers: {
    onCard?: (outcome: DispatchOutcome) => void;
    /** Files two successful cards both changed. Only one version came back. */
    onConflict?: (paths: string[]) => void;
    onDone?: (summary: { worked: number; error?: string }) => void;
    onError?: (message: string) => void;
  },
  signal?: AbortSignal,
): Promise<void> {
  let res: Response;
  try {
    res = await fetch(apiUrl("/api/kanban/run"), {
      method: "POST",
      headers: authHeaders(),
      body: JSON.stringify(req),
      signal,
    });
  } catch (err) {
    handlers.onError?.(err instanceof Error ? err.message : "network error");
    return;
  }
  if (!res.ok || !res.body) {
    handlers.onError?.(await streamRefusal(res));
    return;
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer = (buffer + decoder.decode(value, { stream: true })).replace(/\r\n/g, "\n");
    let sep: number;
    while ((sep = buffer.indexOf("\n\n")) !== -1) {
      const block = buffer.slice(0, sep);
      buffer = buffer.slice(sep + 2);
      let event = "";
      let payload = "";
      for (const line of block.split("\n")) {
        if (line.startsWith("event:")) event = line.slice(6).trim();
        else if (line.startsWith("data:")) payload += line.slice(5).trim();
      }
      if (!payload) continue;
      try {
        const data = JSON.parse(payload);
        if (event === "card") handlers.onCard?.(data as DispatchOutcome);
        else if (event === "conflict") handlers.onConflict?.((data as { paths: string[] }).paths);
        else if (event === "done") handlers.onDone?.(data as { worked: number; error?: string });
      } catch {
        // A frame we cannot parse is one frame lost, not a broken dispatch: the board is the
        // source of truth and a refetch will show what actually happened.
      }
    }
  }
}
export const getProjects = () => json<ProjectState[]>("/api/projects");
export const startProject = (req: {
  spec: string;
  workspace?: string | null;
  max_iterations?: number;
  auto_approve?: boolean;
}) => json<ProjectState>("/api/projects", { method: "POST", body: JSON.stringify(req) });
/** Advance one iteration. There is no server-side run loop on purpose: a client that has this can
 *  loop it, stop between iterations, and show the state after each one. */
export const stepProject = (id: string) =>
  json<ProjectState>(`/api/projects/${encodeURIComponent(id)}/step`, { method: "POST" });
export const getProject = (id: string) =>
  json<{ state: ProjectState; columns: Record<string, TaskCard[]> }>(`/api/projects/${id}`);
export const approveProject = (id: string, card?: string) =>
  json<ProjectState>(`/api/projects/${id}/approve`, {
    method: "POST",
    body: JSON.stringify({ card: card ?? null }),
  });
export const denyProject = (id: string, card: string) =>
  json<ProjectState>(`/api/projects/${id}/deny`, { method: "POST", body: JSON.stringify({ card }) });




// --- Runs (in-app autonomous run trigger, streamed) ---

export interface RunRequestInput {
  task: string;
  verify?: string | null;
  workspace?: string | null;
  max_attempts?: number;
  // An approved/edited plan (raw text from the preview). When set, the run uses THIS plan verbatim
  // instead of re-planning. Omitted = the run plans for itself (current behaviour).
  plan?: string | null;
  // The worker's model slug (omitted / null = the configured default) and the routing mode.
  model?: string | null;
  fuse?: boolean;
  cascade?: boolean;
  // How far the agent may reach and when it stops to ask. The server resolves it into tool denials
  // and pause flags, and mints a thread when a pause is asked for and none was sent.
  posture?: { reach: Reach; approval: Approval } | null;
  // Which tier each ROLE draws from. Not a claim that routing helps — see bench/role_routing.
  profile?: Profile | null;
  /** One model slug per role, overriding whatever the profile resolves that role to. Omitted keys
   *  keep the profile's answer, which is why an unset picker must send nothing rather than "". */
  roles?: Partial<Record<"explore" | "plan" | "edit" | "review", string>> | null;
  // Who chose `profile`: "user" or "system". The launcher asks again as of this release; a form
  // nobody touched still says "system", because the receipt must not record a default as a decision.
  profile_source?: string;
  /** Dollar ceiling per ATTEMPT, not per run: the worker is called once per attempt and each call
   *  gets a fresh allowance, so this times `max_attempts` is what a run can cost. Named that way on
   *  the server too (`CodeSeams.max_usd`), because the field name alone does not say it. */
  max_usd?: number | null;
}


/** One live progress frame from the run loop (an AgentEvent, serialized). `kind` picks the shape. */
export interface RunEvent {
  kind: string;
  text: string;
  index?: number;
  max_attempts?: number;
  success?: boolean;
  detail?: string;
  // `kind === "edit"`: the REAL unified diff of a file the agent just changed this step (never
  // fabricated — read from the file on disk before/after the write-tool call).
  path?: string;
  patch?: string;
}

/** The terminal `done` payload of a run. */
export interface RunDone {
  success: boolean;
  answer: string;
  attempts: number;
  // "cancelled" when a cooperative Stop ended the run between attempts; "" for an ordinary finish.
  stopped_reason?: string;
}

/** What is about to judge this run, delivered BEFORE the first step.
 *
 *  `source` is `user` when someone typed the command, `inferred:<file>` when the project was read for
 *  it, and `none` when nothing executable was found. That last case is the one this frame exists for:
 *  it has always been true whenever the box was left empty, and the interface never once said it. */
export interface RunVerify {
  command: string | null;
  source: string;
}

export interface RunStreamHandlers {
  onEvent?: (e: RunEvent) => void;
  /** Arrives once, before the run starts — what will judge it, and who chose that. */
  onVerify?: (v: RunVerify) => void;
  onDone?: (d: RunDone) => void;
  onError?: (msg: string) => void;
  // The run's id, delivered on the first `run` frame — the handle for POST /api/runs/{id}/cancel.
  onRunId?: (id: string) => void;
  // The run stopped for a human verdict. Arrives INSTEAD of `onDone`.
  onPaused?: (p: PausedRun) => void;
}

/** Trigger an autonomous run and stream its live progress. Mirrors {@link streamChat}: the API's SSE
 *  lives on a POST, so we read the response body ourselves and parse `event`/`done`/`error` frames.
 *  This WRITES files and runs the verify command in the workspace (same as `chimera solve`). */
export async function streamRun(
  req: RunRequestInput,
  handlers: RunStreamHandlers,
  signal?: AbortSignal,
): Promise<void> {
  let res: Response;
  try {
    res = await fetch(apiUrl("/api/runs"), {
      method: "POST",
      headers: authHeaders(),
      body: JSON.stringify(req),
      signal,
    });
  } catch (err) {
    handlers.onError?.(err instanceof Error ? err.message : "network error");
    return;
  }
  if (!res.ok || !res.body) {
    handlers.onError?.(await streamRefusal(res));
    return;
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer = (buffer + decoder.decode(value, { stream: true })).replace(/\r\n/g, "\n");
    let sep: number;
    while ((sep = buffer.indexOf("\n\n")) !== -1) {
      dispatchRun(buffer.slice(0, sep), handlers);
      buffer = buffer.slice(sep + 2);
    }
  }
  if (buffer.trim()) dispatchRun(buffer, handlers);
}

function dispatchRun(frame: string, h: RunStreamHandlers): void {
  let event = "message";
  let data = "";
  for (const line of frame.split("\n")) {
    if (line.startsWith("event:")) event = line.slice(6).trim();
    else if (line.startsWith("data:")) data += line.slice(5).trim();
  }
  if (!data) return;
  let payload: Record<string, unknown>;
  try {
    payload = JSON.parse(data);
  } catch {
    return;
  }
  if (event === "run") h.onRunId?.(payload.run_id as string);
  else if (event === "verify") h.onVerify?.(payload as unknown as RunVerify);
  else if (event === "event") h.onEvent?.(payload as unknown as RunEvent);
  else if (event === "done") h.onDone?.(payload as unknown as RunDone);
  else if (event === "paused") h.onPaused?.(payload as unknown as PausedRun);
  else if (event === "error") h.onError?.(payload.message as string);
}

// --- Code conversation (turns that edit, streamed) ---

export interface CodeTurnInput {
  message: string;
  // The conversation this turn belongs to. Omit to start a new one; the id arrives in the first
  // frame and every later turn MUST send it back, or each message starts from nothing.
  session_id?: string | null;
  workspace?: string | null;
  model?: string | null;
  stream?: boolean;
  // The file open in the viewer. Two real effects server-side: it focuses which AGENTS.md files
  // apply, and it is what a compaction restores. Only the path travels — the agent re-reads it.
  open_file?: string | null;
  max_steps?: number | null;
  context_budget?: number | null;
  /** Dollar ceiling for this turn. The loop refuses the call that would cross it, BEFORE making it,
   *  and keeps what it already has. Omit for no ceiling — the behaviour every earlier client had.
   *
   *  Sent per turn, like `context_budget`: the agent is rebuilt from this request each time, so a
   *  ceiling sent once is a ceiling that applied once. */
  max_usd?: number | null;
  repo_map?: boolean;
  explorer?: boolean;
  posture?: { reach: Reach; approval: Approval } | null;
  profile?: Profile | null;
  /** Ids from {@link uploadAttachment}. This turn only — an image re-sent on every later turn is
   *  paid for again each time, for what is, after the first answer, no new information. */
  attachments?: string[];
  /** Route this turn through the fusion panel. It will not be able to use tools — see
   *  {@link CodeTurnDone.fused}, which is how the answer says so. */
  fuse?: boolean;
  /** Hand this turn to an EXTERNAL coding agent over ACP — "claude", "gemini" or "custom".
   *
   *  Omit for Chimera's own loop. The events are identical either way, deliberately: the checkpoint,
   *  the verifier and the revert are what this screen is, and they apply to any worker. What does
   *  NOT apply is prevention — see {@link PostureFacts.external_agent}. */
  provider?: string | null;
  /** The command for `provider: "custom"`. Split shell-style and run WITHOUT a shell. */
  provider_command?: string | null;
}

/** One tool call, as it happens. `arguments` and `observation` arrive already clipped server-side
 *  and SAY so when they were — the UI must never present a truncated observation as complete. */
export interface CodeToolEvent {
  name: string;
  arguments: Record<string, string>;
  ok: boolean;
  observation: string;
}

/** The terminal `done` payload of a coding turn — what it did, what it cost, how close to the wall. */
export interface CodeTurnDone {
  answer: string;
  // Null for an external turn: the steps happened inside somebody else's loop and it did not say
  // how many. Zero would read as "it did nothing".
  steps: number | null;
  stopped_reason: string;
  tool_names: string[];
  model: string;
  prompt_tokens: number;
  completion_tokens: number;
  usd: number | null;
  // The largest prompt this turn built. The number that says whether raising max_steps is safe —
  // shown rather than hidden, because a ceiling raised without seeing its cost is a trap.
  context_peak_tokens: number | null;
  /** Output tokens per second of time spent INSIDE the model calls — measured per step, not divided
   *  out of the turn's duration, which would fold the tools and the verifier into the model's speed.
   *  Null when nothing was measured; zero would say the model produced nothing. */
  tokens_per_second?: number | null;
  // Typed as the real shape rather than an opaque bag: the fusion panel renders it, and an opaque
  // record forced every consumer to cast — which is how a field silently stops being rendered.
  route_meta: RouteMeta | null;
  /** This turn read untrusted content. Reported because a turn steered by a planted instruction
   *  otherwise looks exactly like one that was not. */
  tainted?: boolean;
  /** Facts recalled from long-term memory for this turn, and which retrieval layer produced them.
   *  Never guessed: a layer that returned nothing is not named. */
  memory_facts_used?: number;
  memory_layer?: string | null;
  /** The durable fact this turn SAVED, from an explicit "remember that…" the user typed, or null.
   *  Reported rather than silent: a fact saved without a word changes every future conversation,
   *  and the person who caused it never saw it happen. */
  memory_saved?: string | null;
  /** Redundant facts merged away after that write, when "Tidy memory" is on. Zero while memory is
   *  under its budget — which is most of the time, and costs no model call to establish. */
  memory_consolidated?: number;
  /** This turn went through the fusion panel and therefore could NOT use tools — it answered from
   *  the prompt alone. Zero tool calls is the same number a turn that needed none reports, so
   *  without this flag the two are indistinguishable. */
  fused?: boolean;
  /** The external agent that did this turn, or absent for Chimera's own loop.
   *
   *  Present because the two are not interchangeable on the receipt: `steps` and
   *  `context_peak_tokens` arrive as null for an external turn — those numbers exist inside somebody
   *  else's loop and it did not report them — and zero would read as "it did nothing". */
  external?: string;
  /** Permission prompts answered on your behalf. Recorded rather than hidden: we grant them because
   *  gating a prompt the agent did not have to ask is theatre, and the honest half of that bargain
   *  is that every grant is on the receipt. */
  auto_approved?: string[];
  /** Writes the agent asked us to make and the write region refused. A refusal nobody sees is
   *  indistinguishable from a write that silently did not happen. */
  refused_writes?: string[];
}

/** The verdict on what a turn WROTE, emitted only when the turn edited something.
 *
 *  `state: "none"` is not an omission — it is the project having no verification command, said out
 *  loud, because the alternative is a user assuming the edits were checked when nothing checked
 *  them. `revert_token` is present only on `"failed"`, and the undo is OFFERED rather than applied:
 *  silently undoing what someone watched being typed is a worse surprise than a failing test. */
export interface CodeVerified {
  command: string | null;
  source: string;
  state: "none" | "passed" | "failed" | "abstained";
  output?: string;
  revert_token?: string;
}

export interface CodeTurnHandlers {
  onSession?: (id: string) => void;
  onToken?: (text: string) => void;
  onTool?: (e: CodeToolEvent) => void;
  onEdit?: (path: string, patch: string) => void;
  onVerified?: (v: CodeVerified) => void;
  onDone?: (d: CodeTurnDone) => void;
  onError?: (msg: string) => void;
}

/** One file handed to the agent: an image to look at, or a document converted to text on arrival.
 *
 *  The response never carries the content back — `chars` is how much text a document yielded, which
 *  is the honest way to say "we read it": a document that converted to nothing is not the same as
 *  one we never opened, and `note` carries the reason when there is one. */
export interface Attachment {
  id: string;
  name: string;
  kind: string;
  chars: number;
  note: string;
}

export async function uploadAttachment(file: File): Promise<Attachment> {
  const body = new FormData();
  body.append("file", file);
  const res = await fetch(apiUrl("/api/attachments"), {
    method: "POST",
    headers: authHeadersNoContentType(),
    body,
  });
  if (!res.ok) throw new Error(await refusal(res));
  return (await res.json()) as Attachment;
}

/** Whether the model that answers a turn can look at an image.
 *
 *  Three states, and `unknown` is the important one: the source is a lookup table, and a table that
 *  has never heard of a model must not be allowed to report it as blind — someone would go and turn
 *  off a capability that works. */
export interface VisionSupport {
  model: string;
  support: "yes" | "no" | "unknown";
}

/** Can THIS model look at an image? `model` is the one the composer has picked; omitted asks about
 *  the install's default, which is what a caller with no picker means.
 *
 *  It used to take no argument, and the moment a per-conversation picker existed that made the
 *  warning under the paperclip describe a model the turn was not going to use. */
export const getVisionSupport = (model?: string) =>
  json<VisionSupport>(`/api/vision${model ? `?model=${encodeURIComponent(model)}` : ""}`);

/** Whether speech can become text on this machine, and by which route.
 *
 *  Asked before the microphone opens. Recording, uploading and then failing tells someone their
 *  audio could not be transcribed — which reads as "the recording was bad" rather than "nothing was
 *  ever going to transcribe it". */
export interface DictationSupport {
  support: "yes" | "no";
  how: "local" | "openai" | "";
}

export const getDictationSupport = () => json<DictationSupport>("/api/dictation");

/** Dictated speech, as text. `note` is non-empty when transcription could not be done — which the
 *  composer shows rather than pasting an error message in as if it were what you said. */
export interface Transcript {
  text: string;
  note: string;
}

export async function transcribe(audio: Blob): Promise<Transcript> {
  const body = new FormData();
  body.append("file", audio, "speech.webm");
  const res = await fetch(apiUrl("/api/transcribe"), {
    method: "POST",
    headers: authHeadersNoContentType(),
    body,
  });
  if (!res.ok) throw new Error(await refusal(res));
  return (await res.json()) as Transcript;
}

/** Undo the edits of a turn whose verification failed. Single-use: the token is consumed by the
 *  server, so a second press cannot restore a snapshot the user has since typed on top of. */
export async function revertCodeTurn(
  token: string,
): Promise<{ ok: boolean; restored: number; left_new_files?: boolean }> {
  // `left_new_files`: the restore put the captured content back but did NOT remove files the turn
  // created. Inside a git repository that pass is skipped unconditionally — deliberately, after a
  // path bug once let a revert wipe a repo — which is most workspaces someone opens in this app.
  // Optional so an older server, which sends neither field, is not read as `false`.
  return json<{ ok: boolean; restored: number; left_new_files?: boolean }>(
    `/api/code/revert/${token}`,
    { method: "POST" },
  );
}

/** Send one turn of a coding conversation and stream it. Mirrors {@link streamRun}: the SSE lives
 *  on a POST, so we read the body and parse the frames ourselves.
 *
 *  This WRITES files in the workspace, like the run trigger — the difference is not permission, it
 *  is that a turn is a conversation step (fast, no verify-or-revert) while a run is a transaction. */
export async function streamCodeTurn(
  req: CodeTurnInput,
  handlers: CodeTurnHandlers,
  signal?: AbortSignal,
): Promise<void> {
  let res: Response;
  try {
    res = await fetch(apiUrl("/api/code/turn"), {
      method: "POST",
      headers: authHeaders(),
      body: JSON.stringify(req),
      signal,
    });
  } catch (err) {
    handlers.onError?.(err instanceof Error ? err.message : "network error");
    return;
  }
  if (!res.ok || !res.body) {
    handlers.onError?.(await streamRefusal(res));
    return;
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer = (buffer + decoder.decode(value, { stream: true })).replace(/\r\n/g, "\n");
    let sep: number;
    while ((sep = buffer.indexOf("\n\n")) !== -1) {
      dispatchCodeTurn(buffer.slice(0, sep), handlers);
      buffer = buffer.slice(sep + 2);
    }
  }
  if (buffer.trim()) dispatchCodeTurn(buffer, handlers);
}

function dispatchCodeTurn(frame: string, h: CodeTurnHandlers): void {
  let event = "message";
  let data = "";
  for (const line of frame.split("\n")) {
    if (line.startsWith("event:")) event = line.slice(6).trim();
    // A token delta can legitimately be whitespace, so this one is NOT trimmed — trimming it turns
    // streamed prose into a wall of runtogetherwords.
    else if (line.startsWith("data:")) data += line.slice(5).replace(/^ /, "");
  }
  if (!data) return;
  let payload: Record<string, unknown>;
  try {
    payload = JSON.parse(data);
  } catch {
    return;
  }
  if (event === "session") h.onSession?.(payload.session_id as string);
  else if (event === "token") h.onToken?.(payload.text as string);
  else if (event === "tool") h.onTool?.(payload as unknown as CodeToolEvent);
  else if (event === "edit") h.onEdit?.(payload.path as string, payload.patch as string);
  else if (event === "verified") h.onVerified?.(payload as unknown as CodeVerified);
  else if (event === "done") h.onDone?.(payload as unknown as CodeTurnDone);
  else if (event === "error") h.onError?.(payload.message as string);
}

// --- Posture (how far the agent reaches, and when it stops to ask) ---

export type Reach = "read_only" | "workspace" | "workspace_shell";
export type Approval = "always" | "suspicious" | "never";

/** What the chosen posture means on THIS machine, right now. Structured rather than prose so the
 *  sentence can be rendered in every language — a server that returned English would make this the
 *  one untranslated line on the screen. */
export interface PostureFacts {
  writes: "nothing" | "workspace";
  workspace: string;
  shell: "none" | "isolated" | "host" | "asks" | "refused";
  pauses: "always" | "tainted" | "never";
  // True when the shell would run on this machine while the config asked for a container. The one
  // case where the honest answer contradicts the user's setup, so it is never folded into `shell`.
  fell_back_to_host: boolean;
  // True when this surface has NO taint ledger: nothing marks the conversation after it reads
  // untrusted content, so the tools that would otherwise start refusing keep working. The default
  // for a chat, deliberately — and therefore something the sentence has to admit.
  unguarded: boolean;
  // The external agent doing the work, or "" for Chimera's own loop.
  //
  // When set, every field above changes meaning. An ACP agent has file and shell tools of its own:
  // it MAY route a write through our handler, where the write region applies exactly as it does
  // natively — and it may not, in which case the region applies to nothing. So `writes` and `shell`
  // stop being boundaries and become descriptions of the calls we happened to see. What survives is
  // the checkpoint, and the sentence has to promise that instead.
  external_agent: string;
}

/** Ask what a posture would mean, without committing to it.
 *
 *  A POST, and never cached: it reports the LIVE state of the sandbox, so a Docker daemon that died
 *  since the last call has to change the answer rather than be served from a cache. */
export const getPostureFacts = (
  reach: Reach,
  approval: Approval,
  workspace?: string | null,
  surface: "run" | "turn" | "chat" = "turn",
  // Which external agent this posture applies to, if any. Sent because it changes what every other
  // field MEANS: an ACP agent has file tools of its own, so the write region describes the calls we
  // see rather than the ones that happen.
  provider?: string | null,
) =>
  json<PostureFacts>("/api/code/posture", {
    method: "POST",
    body: JSON.stringify({
      reach,
      approval,
      workspace: workspace || null,
      surface,
      provider: provider || null,
    }),
  });

// --- Roles (which model does which job) ---

export type Profile = "economy" | "balanced" | "max";

/** One model slug per role. `verify` is absent rather than nullable: offering a field would imply a
 *  choice exists, and the whole value of an executable verifier is that there isn't one. */
export interface RoleModels {
  explore: string | null;
  plan: string | null;
  edit: string | null;
  review: string | null;
  // Only the two TOOL-FREE turns can be fused. A `fuse` on the coding loop would never fire — the
  // router sends any turn carrying tool schemas to a single model — and would report that it had.
  fuse_plan: boolean;
  fuse_review: boolean;
}

/** The concrete slugs a profile resolves to. Asked of the server rather than mirrored here: the
 *  tiers honour the user's cost mode and per-tier settings, and a second copy of that resolution in
 *  TypeScript would display a model the run does not actually use. */
export const getRoleModels = (profile: Profile) =>
  json<RoleModels>("/api/code/roles", {
    method: "POST",
    body: JSON.stringify({ profile }),
  });

// --- "Was it worth it?" (what each profile cost, and what it got) ---

export interface ProfileWorth {
  profile: string | null;
  runs: number;
  passed: number;
  // Of `passed`, how many an EXECUTABLE command judged. The rest were approved by a model reading
  // the answer text, which never sees the diff, the transcript, or a file. Both really passed; only
  // one was verified, and the panel exists to say whether a configuration earned its cost.
  passed_by_verifier: number;
  reverted: number;
  // Runs whose SUCCESSFUL attempt changed no file — the empty-patch failure, kept apart from
  // `passed` so a configuration cannot look good at the thing it is bad at.
  unproductive: number;
  attempts_total: number;
  // Null when ANY run in the group had an unknown cost. `usd_known_runs` is the denominator:
  // without it, a null is indistinguishable from "no data" and a number from "all of it".
  usd_total: number | null;
  usd_known_runs: number;
}

export interface WorthReport {
  profiles: ProfileWorth[];
  total_runs: number;
  readable_n: number;
  any_readable: boolean;
}

/** What each configuration actually cost and got, over the runs that really happened here.
 *
 *  The groups arrive sorted BY NAME, never by outcome — they are observational (different tasks,
 *  different days, no randomisation), and ordering them by pass rate would read as a ranking these
 *  numbers cannot support. The comparison that can is the paired A/B in bench/role_routing. */
export const getWorth = (workspace?: string) =>
  json<WorthReport>(
    `/api/code/worth${workspace ? `?workspace=${encodeURIComponent(workspace)}` : ""}`,
  );

/** One past coding conversation, as a sidebar row. */
export interface CodeSessionMeta {
  id: string;
  title: string;
  /** The project it was about. Empty means the server's own workspace. */
  workspace: string;
  turns: number;
  updated_at: number;
}

/** Past coding conversations, newest first, each carrying the project it belongs to.
 *
 * `workspace` is what makes the list groupable. Without it these are a flat pile of old questions
 * with no owner — you can see that you asked something on Tuesday but not which codebase about. */
export const listCodeSessions = () => json<CodeSessionMeta[]>("/api/code/sessions");

/** Sub-directories of `path` (home when empty), for picking a project by clicking.
 *
 * Directories only, nothing read — it enumerates folder names. The tree endpoint cannot answer this
 * because it is scoped inside a workspace, and the question here is which workspace. */
export const browseDirs = (path: string) =>
  json<{ path: string; parent: string; entries: { name: string; path: string }[]; capped: boolean }>(
    `/api/fs/browse?path=${encodeURIComponent(path)}`,
  );

/** A stored conversation, already folded into exchanges by the backend.
 *
 * The fold (model messages → "I asked this, it did these things, it answered that") lives on the
 * server because it is a rule about the model's wire format, and because its edge cases — a tool
 * result whose call was trimmed, arguments that are not valid JSON — are worth testing where the
 * transcript is produced rather than re-derived here.
 *
 * `edits` comes back empty on a replay: a diff was streamed live and never entered the message
 * list, so a resumed turn shows the tool call that wrote a file, not the coloured patch. */
export const getCodeSession = (sessionId: string) =>
  json<{
    id: string;
    workspace: string;
    exchanges: {
      you: string;
      answer: string;
      tools: CodeToolEvent[];
      edits: { path: string; patch: string }[];
    }[];
  }>(`/api/code/sessions/${encodeURIComponent(sessionId)}`);

/** Forget a coding conversation. An unknown id is `{ok:false}`, not an error — that is exactly the
 *  state a second click on Clear hits. */
export const deleteCodeSession = (sessionId: string) =>
  json<{ ok: boolean }>(`/api/code/sessions/${encodeURIComponent(sessionId)}`, {
    method: "DELETE",
  });

/** Branch a conversation into a new one, and get the new one's sidebar row back.
 *
 * A conversation is a linear message list that each turn replaces, so trying a different approach
 * costs the thread you were on. The row returned is the FORK's — resuming with the parent's would
 * put you back in the conversation you just branched away from. */
export const forkCodeSession = (sessionId: string) =>
  json<CodeSessionMeta>(`/api/code/sessions/${encodeURIComponent(sessionId)}/fork`, {
    method: "POST",
  });

/** The conversation's stored file, unparsed.
 *
 * `getCodeSession` folds messages into exchanges, which is what a screen should render and the
 * wrong thing to debug with: a message the parser dropped is invisible in it. */
export const getCodeSessionRaw = (sessionId: string) =>
  json<CodeSessionRaw>(`/api/code/sessions/${encodeURIComponent(sessionId)}/raw`);

/** A run that stopped before finalizing and is waiting for a human verdict. Arrives on the stream's
 *  `paused` frame INSTEAD of `done` — a pause is not a verdict, and treating it as a failed run
 *  would quietly throw away work that is sitting there to be released. */
export interface PausedRun {
  thread_id: string;
  answer: string;
  tainted?: boolean;
}

/** Every run parked awaiting a verdict, including ones this window never witnessed. */
export const getPausedRuns = () => json<PausedRun[]>("/api/runs/paused");

/** The four HITL actions, mirroring the core's LangGraph `HumanInterrupt` envelope. */
export type HitlAction = "accept" | "edit" | "respond" | "ignore";

/** Record a verdict on a paused run. This does NOT conclude it: every action needs the run to be
 *  resumed (POST /api/runs with the same thread_id), which is where the answer is finalized. Only
 *  `respond` spends another attempt — `retries` says so, so the UI can warn before committing it. */
export const respondRun = (
  threadId: string,
  action: HitlAction,
  body: { answer?: string; feedback?: string } = {},
) =>
  json<{ ok: boolean; resume_required: boolean; retries: boolean }>(
    `/api/runs/${encodeURIComponent(threadId)}/respond`,
    { method: "POST", body: JSON.stringify({ action, ...body }) },
  );

/** Cooperatively cancel an in-flight run: the loop halts BEFORE its next attempt (an in-flight model
 *  step can't be interrupted). A finished/unknown id is a no-op {ok:false}, never an error. */
export const cancelRun = (runId: string) =>
  json<{ ok: boolean }>(`/api/runs/${encodeURIComponent(runId)}/cancel`, { method: "POST" });

// --- Agents (the Agent Manager: a parallel batch of isolated autonomous runs, streamed) ---

export interface AgentTaskInput {
  task: string;
  // A shell command that judges THIS task (exit 0 = pass), run in the task's isolated worktree.
  verify?: string | null;
}

export interface AgentsRequestInput {
  tasks: AgentTaskInput[];
  workspace?: string | null;
  max_workers?: number;
  // The worker's model slug (omitted / null = the configured default) and the routing mode.
  model?: string | null;
  fuse?: boolean;
  cascade?: boolean;
  // Sent, never omitted. An absent posture resolves server-side to no tool denials and no pause,
  // and an absent profile to a Manager reviewing with the very model that wrote the patch — so a
  // task run as one of several would be quietly weaker than the same task run alone.
  posture?: { reach: Reach; approval: Approval } | null;
  profile?: Profile | null;
}

/** The `start` frame: the batch is underway (the task list + the resolved workspace). */
export interface AgentsStart {
  tasks: string[];
  workspace: string;
  max_workers: number;
}

/** One live progress frame, TAGGED with `index` so the board routes it to the right task card. It's a
 *  run `event` (an AgentEvent, serialized) — same shape as {@link RunEvent}, which already carries the
 *  optional `index`. */
export type AgentTaggedEvent = RunEvent & { index: number };

export interface AgentsStreamHandlers {
  onStart?: (s: AgentsStart) => void;
  onEvent?: (e: AgentTaggedEvent) => void;
  onBatchDone?: (b: AgentsBatch) => void;
  onError?: (msg: string) => void;
  // The batch's id, delivered on the first `batch` frame — the handle for
  // POST /api/agents/{id}/cancel (mirrors {@link RunStreamHandlers.onRunId}).
  onBatchId?: (id: string) => void;
}

/** Trigger a parallel batch of autonomous runs (each in its OWN git worktree) and stream progress.
 *  Mirrors {@link streamRun}: the SSE lives on a POST, so we read the body and parse the frames —
 *  `start`, per-task tagged `event`s, a terminal `batch_done`, or `error`. This WRITES files and runs
 *  each verify command in the workspace (same capability as `chimera solve-batch`). Isolation is REAL
 *  only in a git repo; `batch_done.is_repo === false` means the tasks ran in-place, without it. */
export async function streamAgents(
  req: AgentsRequestInput,
  handlers: AgentsStreamHandlers,
  signal?: AbortSignal,
): Promise<void> {
  let res: Response;
  try {
    res = await fetch(apiUrl("/api/agents"), {
      method: "POST",
      headers: authHeaders(),
      body: JSON.stringify(req),
      signal,
    });
  } catch (err) {
    handlers.onError?.(err instanceof Error ? err.message : "network error");
    return;
  }
  if (!res.ok || !res.body) {
    handlers.onError?.(await streamRefusal(res));
    return;
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer = (buffer + decoder.decode(value, { stream: true })).replace(/\r\n/g, "\n");
    let sep: number;
    while ((sep = buffer.indexOf("\n\n")) !== -1) {
      dispatchAgents(buffer.slice(0, sep), handlers);
      buffer = buffer.slice(sep + 2);
    }
  }
  if (buffer.trim()) dispatchAgents(buffer, handlers);
}

function dispatchAgents(frame: string, h: AgentsStreamHandlers): void {
  let event = "message";
  let data = "";
  for (const line of frame.split("\n")) {
    if (line.startsWith("event:")) event = line.slice(6).trim();
    else if (line.startsWith("data:")) data += line.slice(5).trim();
  }
  if (!data) return;
  let payload: Record<string, unknown>;
  try {
    payload = JSON.parse(data);
  } catch {
    return;
  }
  if (event === "batch") h.onBatchId?.(payload.batch_id as string);
  else if (event === "start") h.onStart?.(payload as unknown as AgentsStart);
  else if (event === "event") h.onEvent?.(payload as unknown as AgentTaggedEvent);
  else if (event === "batch_done") h.onBatchDone?.(payload as unknown as AgentsBatch);
  else if (event === "error") h.onError?.(payload.message as string);
}

/** Cooperatively cancel tasks in an in-flight batch: `index` stops just that task, omitting it (null)
 *  stops every task. Each task halts BEFORE its next attempt (an in-flight model step can't be
 *  interrupted). A finished/unknown batch is a no-op {ok:false, cancelled:0}, never an error. */
export const cancelAgents = (batchId: string, index?: number | null) =>
  json<{ ok: boolean; cancelled: number }>(`/api/agents/${encodeURIComponent(batchId)}/cancel`, {
    method: "POST",
    body: JSON.stringify({ index: index ?? null }),
  });

// --- Command runner (workspace-scoped, streamed; fresh subprocess per command — NOT a terminal) ---

export interface ExecRequestInput {
  command: string;
  workspace?: string | null;
  cwd?: string;
  timeout?: number;
}

export interface ExecStreamHandlers {
  /** The run's id, first thing on the wire — so a Stop button exists before there is any output. */
  onStarted?: (id: string) => void;
  onLine?: (text: string) => void;
  onExit?: (code: number) => void;
  onError?: (msg: string) => void;
}

/** Run one command and stream its combined stdout+stderr line by line, then the exit code. Mirrors
 *  {@link streamRun}: the SSE lives on a POST, so we read the body and parse `line`/`exit` frames.
 *  Each call is a FRESH subprocess on the host (or the configured sandbox) — cwd/env don't persist. */
export async function streamExec(
  req: ExecRequestInput,
  handlers: ExecStreamHandlers,
  signal?: AbortSignal,
): Promise<void> {
  let res: Response;
  try {
    res = await fetch(apiUrl("/api/fs/exec"), {
      method: "POST",
      headers: authHeaders(),
      body: JSON.stringify(req),
      signal,
    });
  } catch (err) {
    handlers.onError?.(err instanceof Error ? err.message : "network error");
    return;
  }
  if (!res.ok || !res.body) {
    handlers.onError?.(await streamRefusal(res));
    return;
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer = (buffer + decoder.decode(value, { stream: true })).replace(/\r\n/g, "\n");
    let sep: number;
    while ((sep = buffer.indexOf("\n\n")) !== -1) {
      dispatchExec(buffer.slice(0, sep), handlers);
      buffer = buffer.slice(sep + 2);
    }
  }
  if (buffer.trim()) dispatchExec(buffer, handlers);
}

/** Stop a running command AND everything it started.
 *
 * Aborting the fetch alone would only stop us listening — the server kills the tree when the stream
 * ends, but a browser that has gone away is not a reliable signal, and a Stop button whose effect
 * depends on the network noticing is a Stop button you cannot trust. This asks explicitly, and
 * `cancelled: false` means there was nothing left to stop. */
export const cancelExec = (id: string) =>
  json<{ cancelled: boolean }>("/api/fs/exec/cancel", {
    method: "POST",
    body: JSON.stringify({ id }),
  });

function dispatchExec(frame: string, h: ExecStreamHandlers): void {
  let event = "message";
  let data = "";
  for (const line of frame.split("\n")) {
    if (line.startsWith("event:")) event = line.slice(6).trim();
    else if (line.startsWith("data:")) data += line.slice(5).trim();
  }
  if (!data) return;
  let payload: Record<string, unknown>;
  try {
    payload = JSON.parse(data);
  } catch {
    return;
  }
  if (event === "started") h.onStarted?.(payload.id as string);
  else if (event === "line") h.onLine?.(payload.text as string);
  else if (event === "exit") h.onExit?.(payload.code as number);
  else if (event === "error") h.onError?.(payload.message as string);
}

// --- Orchestration (the hierarchy: plan, run, stop, ledger) ----------------------------------

export interface HierarchyPreviewInput {
  task: string;
  workspace?: string | null;
  max_workers?: number;
  budget?: number | null;
}

export interface HierarchyRunInput {
  task: string;
  /** The folder the workers read. Without it they read the app's own workspace — a different
   *  folder than the one this screen shows, with nothing saying so. */
  workspace?: string | null;
  /** A plan id from `previewHierarchy`. With one, the run executes THAT decomposition instead of
   *  producing a new one — decomposition runs at a non-zero temperature, so asking twice is how a
   *  preview promises one worker and the run delivers three. */
  plan_id?: string;
  max_workers?: number;
  budget?: number | null;
  verifier_model?: string | null;
  fuse?: boolean;
  max_usd?: number | null;
}

/** One frame off the hierarchy stream. `seq` is stamped by the server under a lock and is the
 *  only total order there is — the workers run in parallel and have none to offer. A client that
 *  reconnects replays from the last `seq` it saw; one that sorts by arrival shows a shuffled
 *  fan-out. `kind` is the SSE event name; `data` is that frame's payload. */
export interface OrchFrame {
  seq: number;
  kind: string;
  task_id: string;
  text: string;
  data: Record<string, unknown>;
}

export interface HierarchyStreamHandlers {
  /** The run id, before any work — so a Stop control exists from the first moment. */
  onRunId?: (id: string) => void;
  onFrame?: (frame: OrchFrame) => void;
  onError?: (msg: string) => void;
}

/** What the orchestrator WOULD do, without running a worker.
 *
 *  Not free, and the response says so: on the fan-out branch the top model really is called to
 *  decompose the task (`decompose_spent`). The claim this supports is "no worker tokens". */
export const previewHierarchy = (req: HierarchyPreviewInput) =>
  json<HierarchyPreview>("/api/orchestration/preview", {
    method: "POST",
    body: JSON.stringify(req),
  });

/** Ask a run to stop at its next boundary. Cooperative: a model call already in flight finishes
 *  and is charged. What it saves is every call that had not started yet. An unknown or finished
 *  run is `{ok:false}` with a 200 — the state a stale Stop click lands in, not an error. */
export const cancelOrchestration = (runId: string) =>
  json<{ ok: boolean; cancelled: boolean }>(
    `/api/orchestration/runs/${encodeURIComponent(runId)}/cancel`,
    { method: "POST" },
  );

/** A past orchestration run's transcript from `since` onward.
 *
 *  A fan-out costs a top-model decompose, N workers and a synthesis, and until these were persisted
 *  the whole thing existed only in an SSE stream: closing the tab threw the answer away and kept the
 *  bill. The frames go through the SAME reducer the live stream feeds — `applyFrame` ignores a `seq`
 *  it has already applied — so replaying and then continuing lands on the state a client that never
 *  disconnected would have. */
export const getOrchestrationFrames = (runId: string, since = 0) =>
  json<{ run_id: string; frames: OrchFrame[]; seq: number }>(
    `/api/orchestration/runs/${encodeURIComponent(runId)}?since=${since}`,
  );

/** The runs still on disk, newest first. */
export const getOrchestrationRuns = () =>
  json<{
    runs: {
      run_id: string;
      task: string;
      kind: string;
      started: number;
      frames: number;
      /** From the LAST frames, not from the file merely ending: a run killed with the process
       *  leaves a transcript that stops, and calling that finished would turn a crash into a
       *  completed run in the one list built to find them again. */
      done: boolean;
      /** Unfinished AND not being worked on by the server right now.
       *
       *  `done: false` covered two states a reader has to tell apart. One measured run sat at five
       *  frames for twenty-two minutes with every worker process gone, and on the wire it was
       *  indistinguishable from one that was still thinking. */
      orphaned: boolean;
    }[];
  }>("/api/orchestration/runs");

export const getDelegations = () =>
  json<{ summary: DelegationSummary }>("/api/orchestration/delegations");

/** The ready-made ways of attacking a task a crew can be assembled from.
 *
 *  Fetched rather than bundled, because `instruction` is the system prompt that will actually be
 *  sent: it lives with the rest of the backend's prompts, and changing one must not require
 *  shipping a desktop build. */
export const getApproaches = () =>
  json<{ approaches: CrewApproach[]; default: string[] }>("/api/orchestration/approaches");

export async function streamHierarchy(
  req: HierarchyRunInput,
  handlers: HierarchyStreamHandlers,
  signal?: AbortSignal,
): Promise<void> {
  let res: Response;
  try {
    res = await fetch(apiUrl("/api/orchestration/hierarchy"), {
      method: "POST",
      headers: authHeaders(),
      body: JSON.stringify(req),
      signal,
    });
  } catch (err) {
    handlers.onError?.(err instanceof Error ? err.message : "network error");
    return;
  }
  if (!res.ok || !res.body) {
    handlers.onError?.(await streamRefusal(res));
    return;
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer = (buffer + decoder.decode(value, { stream: true })).replace(/\r\n/g, "\n");
    let sep: number;
    while ((sep = buffer.indexOf("\n\n")) !== -1) {
      dispatchHierarchy(buffer.slice(0, sep), handlers);
      buffer = buffer.slice(sep + 2);
    }
  }
  if (buffer.trim()) dispatchHierarchy(buffer, handlers);
}

function dispatchHierarchy(frame: string, h: HierarchyStreamHandlers): void {
  let event = "message";
  let data = "";
  for (const line of frame.split("\n")) {
    if (line.startsWith("event:")) event = line.slice(6).trim();
    // No trim on the payload: these frames carry prose (the objective, a verifier's objection,
    // the final answer), and trimming each line is how "two words" becomes "twowords".
    else if (line.startsWith("data:")) data += line.slice(5);
  }
  if (!data.trim()) return;
  let payload: Record<string, unknown>;
  try {
    payload = JSON.parse(data);
  } catch {
    return;
  }
  if (event === "error") {
    h.onError?.(payload.message as string);
    return;
  }
  if (event === "run") h.onRunId?.(payload.run_id as string);
  const { seq, task_id: taskId, text, ...rest } = payload;
  h.onFrame?.({
    seq: Number(seq ?? 0),
    kind: event,
    task_id: String(taskId ?? ""),
    text: String(text ?? ""),
    data: rest,
  });
}

// --- Orchestration · the crew (N roles, one task, one worktree each) --------------------------

export interface CrewWorkerInput {
  name: string;
  instruction: string;
}

/** One ready-made way of attacking a task.
 *
 *  `instruction` is untranslated on purpose — it is the prompt that gets sent, and the screen
 *  shows what is sent. The label and the one-line description are translated, keyed by `id`. */
export interface CrewApproach {
  id: string;
  instruction: string;
}

export interface CrewRunInput {
  task: string;
  workers: CrewWorkerInput[];
  workspace?: string | null;
  /** Shell command run in each worker's own checkout; exit 0 merges it. Without one, every
   *  worker that did not crash merges — and workers that touched the same file all lose to the
   *  conflict rule, so a crew with no check usually lands nothing. */
  verify?: string | null;
  max_workers?: number;
  synthesize?: boolean;
}

export async function streamCrew(
  req: CrewRunInput,
  handlers: HierarchyStreamHandlers,
  signal?: AbortSignal,
): Promise<void> {
  let res: Response;
  try {
    res = await fetch(apiUrl("/api/orchestration/crew"), {
      method: "POST",
      headers: authHeaders(),
      body: JSON.stringify(req),
      signal,
    });
  } catch (err) {
    handlers.onError?.(err instanceof Error ? err.message : "network error");
    return;
  }
  if (!res.ok || !res.body) {
    handlers.onError?.(await streamRefusal(res));
    return;
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer = (buffer + decoder.decode(value, { stream: true })).replace(/\r\n/g, "\n");
    let sep: number;
    while ((sep = buffer.indexOf("\n\n")) !== -1) {
      dispatchHierarchy(buffer.slice(0, sep), handlers);
      buffer = buffer.slice(sep + 2);
    }
  }
  if (buffer.trim()) dispatchHierarchy(buffer, handlers);
}
