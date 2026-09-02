/**
 * The four ways one task can be run, as data rather than as four screens.
 *
 * They were four destinations — Runs, Orchestration, Lifecycle, and the crew form reached from
 * inside Orchestration — each with its own task box, its own check field and its own copy of "which
 * folder is this". You picked the destination before you had written the task, which is the wrong
 * order: the task is what decides how it should run, and until it exists there is nothing to decide
 * from. Retyping it into a second box to try the second way is what made trying the second way
 * something nobody did.
 *
 * So the task is typed once and the mode is a choice made beside it. The same move the batch board
 * already made when it stopped being a tab, for the same reason written in `Code.tsx`: it was
 * "a destination chosen before anyone knew whether the work was parallel".
 *
 * The ids are the backend's own names. Naming them again for the UI would be a second vocabulary
 * for one set of things, and the frames, the routes and the run transcripts all speak the first.
 */

export const MODES = ["single", "lifecycle", "hierarchy", "crew"] as const;

export type WorkMode = (typeof MODES)[number];

export const DEFAULT_MODE: WorkMode = "single";

/**
 * Modes where the shared check command is actually sent somewhere.
 *
 * `hierarchy` is the exception and not an oversight: its workers are mounted tool-free, so they
 * read and answer and never touch a file. There is nothing for a shell command to check, and a
 * field that quietly discards what you typed into it is worse than no field — see the note the
 * console shows when a command is already typed and the mode changes under it.
 */
const VERIFY_MODES: readonly WorkMode[] = ["single", "lifecycle", "crew"];

export function usesVerify(mode: WorkMode): boolean {
  return VERIFY_MODES.includes(mode);
}

export function isMode(value: string): value is WorkMode {
  return (MODES as readonly string[]).includes(value);
}

/**
 * The mode named in the URL, or the default.
 *
 * Read straight from the hash rather than through `useRoute`, for the reason `Work.tsx` writes down
 * about its tabs: a deep link that resolved one render late shows the wrong mode and then jumps,
 * which reads as a bug even though it settles correctly.
 */
export function readMode(hash: string): WorkMode {
  const query = hash.split("?")[1] ?? "";
  const named = new URLSearchParams(query).get("mode") ?? "";
  return isMode(named) ? named : DEFAULT_MODE;
}
