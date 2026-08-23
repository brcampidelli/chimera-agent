import { readFileSync, readdirSync, statSync } from "node:fs";
import { join, sep } from "node:path";
import { describe, expect, it } from "vitest";

/**
 * Every helper in `api.ts` is reached from a screen, or it is not in `api.ts`.
 *
 * Seven were not: `streamChat`, `listSessions`, `getSession`, `deleteSession`, `getPlan`,
 * `gitRevert`, `getCompletionStats`. Typed, documented, routed on the server, and called by nothing
 * a user can reach. The `getPlan` docstring even claimed "the CLI and the Runs screen still use it"
 * — the Runs screen imports only `getRuns`, and the CLI does not speak HTTP to its own API.
 *
 * Two were wired, because the capability was worth having: the editor posts every accept and
 * dismiss and nobody was shown the result, and a git panel that can keep a change but not throw one
 * away is half a git panel. Five were deleted, because the chat subsystem they belong to has no
 * screen and typed client helpers for a UI nobody built are the thing being complained about. The
 * server routes stay — they serve the OpenAI-compatible surface and anyone using the API directly.
 *
 * This checks ALL of them rather than a named list. A guard that enumerates what to look at fails
 * open on whatever is added next, which is exactly how the seven accumulated.
 *
 * **It does not stand alone, and that is worth knowing before trusting it.** The search is for the
 * identifier in production source, so an unused IMPORT satisfies it — deleting the last call while
 * leaving `import { getCompletionStats }` in place keeps this green. What closes that hole is
 * `noUnusedLocals` in `tsconfig.json`: the import cannot survive the call, so the typecheck forces
 * the deletion this test then catches. Verified by removing both, which fails here as it should.
 * If that compiler option is ever turned off, this guard quietly weakens with it.
 */

const SRC = join(__dirname, "..");

/** Prose stripped, because prose about a function is not a call to it.
 *
 * Learned three times over in this repo: a comment naming the helper kept an earlier version of
 * this check green while the real caller had been deleted.
 */
function code(source: string): string {
  return source
    .replace(/\{\s*\/\*[\s\S]*?\*\/\s*\}/g, " ")
    .replace(/\/\*[\s\S]*?\*\//g, " ")
    .replace(/(^|[^:])\/\/[^\n]*/g, "$1");
}

function sources(dir: string, out: string[] = []): string[] {
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) sources(full, out);
    else if (/\.tsx?$/.test(entry) && !entry.endsWith(".d.ts")) out.push(full);
  }
  return out;
}

describe("lib/api.ts", () => {
  it("exports nothing that no screen calls", () => {
    const apiPath = join(SRC, "lib", "api.ts");
    const api = code(readFileSync(apiPath, "utf8"));
    const exported = [
      ...api.matchAll(/export (?:const|async function|function) (\w+)\s*[=(<]/g),
    ].map((m) => m[1]);

    expect(exported.length).toBeGreaterThan(50); // the scan has to be finding things

    // Production only: not api.ts, not `*.test.*`, not the shared mocks under `test/`. A helper
    // reached only from a mock is reachable from no screen at all — which is the state all seven
    // were in, and the state the shared mock would otherwise disguise.
    const helpers = `${join(SRC, "test")}${sep}`;
    const elsewhere = sources(SRC)
      .filter((f) => f !== apiPath && !/\.test\.tsx?$/.test(f) && !f.startsWith(helpers))
      .map((f) => code(readFileSync(f, "utf8")))
      .join("\n");

    const orphans = exported.filter((name) => !new RegExp(`\\b${name}\\b`).test(elsewhere));
    expect(orphans).toEqual([]);
  });

  it("no longer carries the chat subsystem it never had a screen for", () => {
    const api = readFileSync(join(SRC, "lib", "api.ts"), "utf8");

    for (const gone of ["streamChat", "listSessions", "deleteSession", "getPlan"]) {
      expect(api).not.toContain(`export const ${gone}`);
      expect(api).not.toContain(`export async function ${gone}`);
    }
  });
});
