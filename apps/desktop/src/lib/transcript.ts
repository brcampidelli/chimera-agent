/** Turning a conversation into text somebody can keep.
 *
 *  Until now the only clipboard call in the entire app copied a `pip install` line. A conversation
 *  that edited someone's repository could not be quoted in a bug report, pasted into a review, or
 *  saved anywhere — the record of what an agent did to a codebase lived only inside a window.
 *
 *  Pure functions over data already in the client, deliberately: no route, no backend, and nothing
 *  here can fail in a way that costs a turn.
 */

/** One exchange, in the shape both the live conversation and a replayed session provide. */
export interface TranscriptExchange {
  you: string;
  answer: string;
  tools?: { name: string; arguments: Record<string, string>; ok: boolean; observation: string }[];
  edits?: { path: string; patch: string }[];
}

export interface TranscriptMeta {
  workspace?: string;
  model?: string;
  /** ISO string. Passed in rather than read here so the output is deterministic under test. */
  exportedAt: string;
}

/** The marker the server puts in an observation it shortened. See `chimera/core/events.py`. */
const CLIP_MARKER = /… \(\+\d+ chars\)/;

function fence(body: string, lang = ""): string {
  // A body containing ``` would end the fence early and the rest of the transcript would render as
  // prose — including, in the worst case, a tool observation continuing into what looks like the
  // user's next message. Longer fences are the standard escape and Markdown nests them correctly.
  let ticks = "```";
  while (body.includes(ticks)) ticks += "`";
  return `${ticks}${lang}\n${body}\n${ticks}`;
}

/** One exchange as Markdown. Exported for the per-answer copy button. */
export function exchangeToMarkdown(e: TranscriptExchange): string {
  const parts = [`### You\n\n${e.you.trim()}`, `### Chimera\n\n${e.answer.trim() || "_(no answer)_"}`];

  if (e.tools?.length) {
    const rows = e.tools.map((tool) => {
      const args = Object.entries(tool.arguments ?? {})
        .map(([k, v]) => `${k}=${v}`)
        .join(" ");
      const head = `- ${tool.ok ? "✓" : "✗"} \`${tool.name}\`${args ? ` ${args}` : ""}`;
      if (!tool.observation) return head;
      // Clipped output is LABELLED as clipped. The server keeps the head and the tail of 400
      // characters, and a transcript that presents that as the whole thing is how somebody
      // concludes a test suite passed from output that was cut before the failures.
      const clipped = CLIP_MARKER.test(tool.observation) ? " _(output clipped by the server)_" : "";
      return `${head}${clipped}\n\n${fence(tool.observation)}`;
    });
    parts.push(`### Tools\n\n${rows.join("\n")}`);
  }

  if (e.edits?.length) {
    const diffs = e.edits.map((d) => `**${d.path}**\n\n${fence(d.patch, "diff")}`);
    parts.push(`### Edits\n\n${diffs.join("\n\n")}`);
  }

  return parts.join("\n\n");
}

/** The whole conversation as one Markdown document. */
export function toMarkdown(exchanges: TranscriptExchange[], meta: TranscriptMeta): string {
  const header = ["# Chimera conversation", ""];
  if (meta.workspace) header.push(`- Workspace: \`${meta.workspace}\``);
  if (meta.model) header.push(`- Model: ${meta.model}`);
  header.push(`- Exported: ${meta.exportedAt}`, `- Exchanges: ${exchanges.length}`, "");
  return `${header.join("\n")}\n${exchanges.map(exchangeToMarkdown).join("\n\n---\n\n")}\n`;
}

/** Best-effort masking of credential-shaped text, for an export built from this window's memory.
 *
 *  The server masks every transcript it stores (`chimera/core/redact.py`), and that copy is what an
 *  export prefers. When it cannot be had (unreachable, empty, shorter) the export falls back to what
 *  is in memory, which is NOT masked. This mirrors the server's SHAPES (credential headers, `Bearer`,
 *  `sk-`/`ghp_`/`xox`/JWT, URL passwords, `name=value` for a sensitive name). It cannot know the
 *  values of the machine's environment variables, which is why the server copy is preferred and why
 *  the export says when it fell back.
 */
const MASK = "[redacted]";
const SENSITIVE = String.raw`(?:[\w-]*(?:api[_-]?key|secret|token|passw(?:or)?d|credential|private[_-]?key)[\w-]*|authorization)`;
const MASK_RULES: [RegExp, string][] = [
  [/(:\/\/[^\s:/@]+:)[^\s@/]+(@)/g, `$1${MASK}$2`],
  [new RegExp(String.raw`([?&]${SENSITIVE}=)[^\s&#"']+`, "gi"), `$1${MASK}`],
  [
    new RegExp(
      String.raw`(^[ \t]*(?:${SENSITIVE}|x-api-key|cookie|proxy-authorization)[ \t]*:[ \t]*)\S.*$`,
      "gim",
    ),
    `$1${MASK}`,
  ],
  [new RegExp(String.raw`(\b${SENSITIVE}[ \t]*[=:][ \t]*["']?)[^\s"',;]{6,}`, "gi"), `$1${MASK}`],
  [/\b(?:sk|pk|rk)-[A-Za-z0-9_-]{16,}/g, MASK],
  [/\bgh[pousr]_[A-Za-z0-9]{20,}/g, MASK],
  [/\bxox[baprs]-[A-Za-z0-9-]{10,}/g, MASK],
  [/\bBearer\s+[A-Za-z0-9._~+/=-]{20,}/gi, MASK],
  [/\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}/g, MASK],
];

export function maskSecrets(text: string): string {
  return MASK_RULES.reduce((out, [rule, to]) => out.replace(rule, to), text);
}

/** Which of two versions of the same conversation to export, and whether they disagreed.
 *
 *  The live `Exchange[]` is not always the whole story: a session that was replayed after a reload,
 *  or one continued in a second window, can hold turns this component never saw. Exporting from
 *  memory would then hand somebody a transcript that is quietly missing the middle — and a
 *  truncated record of what an agent did to a repository is worse than no record, because it will
 *  be believed.
 *
 *  So the stored copy wins when it has more, and the caller is told, rather than the difference
 *  being smoothed over.
 */
export function reconcile<T>(
  inMemory: T[],
  stored: T[] | null,
): { exchanges: T[]; source: "memory" | "stored"; recovered: number } {
  // `>=`, not `>`: the stored copy is masked by the server (`redact_json` on every write) and the one
  // in memory is not, so an export that has both and is not missing anything takes the stored one.
  if (stored && stored.length > 0 && stored.length >= inMemory.length) {
    return { exchanges: stored, source: "stored", recovered: stored.length - inMemory.length };
  }
  return { exchanges: inMemory, source: "memory", recovered: 0 };
}

/** A filename that is safe on every platform we ship to. */
export function transcriptFilename(exportedAt: string): string {
  // Colons are legal on POSIX and forbidden on Windows, and an ISO timestamp is full of them — a
  // download that silently fails to save is the kind of bug nobody reports, they just stop using it.
  return `chimera-conversation-${exportedAt.replace(/[:.]/g, "-")}.md`;
}
