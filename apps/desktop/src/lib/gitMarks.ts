/**
 * What the file tree says about git: a letter on a file, a dot on the folders above it.
 *
 * Read from the status the Changes card and the git panel already ask for (`/api/git/status`,
 * porcelain v1), not from a second route: two readings of one working tree is how a tree comes to say
 * "modified" while the panel beside it says "clean".
 *
 * Pure functions, kept apart from the tree so the rules for "which letter" and "what a folder
 * inherits" are testable without rendering one.
 *
 * Known limit: porcelain paths are relative to the REPOSITORY root, and the tree's are relative to the
 * chosen folder. They are the same when the folder is the repository, which is the usual case; for a
 * folder inside a larger repository the marks simply do not line up and the tree shows none, which is
 * the honest failure (no mark) rather than a wrong one.
 */

/** M modified · A added (staged new) · U untracked · D deleted. */
export type GitMark = "M" | "A" | "U" | "D";

/** The fields of a status row this module reads. */
export interface GitFileLike {
  path: string;
  x: string;
  y: string;
  untracked: boolean;
}

/** Most to least urgent: a folder shows the strongest thing inside it. */
const URGENCY: readonly GitMark[] = ["D", "M", "A", "U"];

/** One status row as a mark. A deletion in either column wins, then an add, then everything else
 *  (modified, renamed, copied, type change, merge conflict) reads as "modified". */
export function markOf(file: GitFileLike): GitMark {
  if (file.untracked) return "U";
  if (file.x === "D" || file.y === "D") return "D";
  if (file.x === "A") return "A";
  return "M";
}

function stronger(a: GitMark | undefined, b: GitMark): GitMark {
  if (a === undefined) return b;
  return URGENCY.indexOf(a) <= URGENCY.indexOf(b) ? a : b;
}

function clean(path: string): string {
  return path.replace(/\\/g, "/").replace(/\/+$/, "");
}

export interface GitMarks {
  files: Map<string, GitMark>;
  /** Folders: every ancestor of a marked file, carrying the strongest mark below it. */
  dirs: Map<string, GitMark>;
  /** Untracked folders git reports as one entry (`src/new/`): everything under them is untracked. */
  untrackedDirs: string[];
}

export function buildGitMarks(files: readonly GitFileLike[]): GitMarks {
  const marks: GitMarks = { files: new Map(), dirs: new Map(), untrackedDirs: [] };
  for (const f of files) {
    const path = clean(f.path);
    if (!path) continue;
    const mark = markOf(f);
    // Git reports a wholly untracked folder as one entry with a trailing slash.
    const isDirEntry = f.untracked && /[/\\]$/.test(f.path);
    const parts = path.split("/");
    // The folders above the entry (and the folder itself, when the entry is one).
    const depth = isDirEntry ? parts.length : parts.length - 1;
    for (let i = 1; i <= depth; i++) {
      const dir = parts.slice(0, i).join("/");
      marks.dirs.set(dir, stronger(marks.dirs.get(dir), mark));
    }
    if (isDirEntry) marks.untrackedDirs.push(path);
    else marks.files.set(path, mark);
  }
  return marks;
}

/** The mark for a tree node, or null when git has nothing to say about it. */
export function markFor(marks: GitMarks | null, path: string, isDir: boolean): GitMark | null {
  if (!marks) return null;
  const p = clean(path);
  const direct = isDir ? marks.dirs.get(p) : marks.files.get(p);
  if (direct) return direct;
  return marks.untrackedDirs.some((d) => p.startsWith(`${d}/`)) ? "U" : null;
}
