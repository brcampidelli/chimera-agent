import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import { NO_OVERRIDE, ROLES, toRoleModels } from "@/components/code/RolesBar";

/**
 * The half of "a model per role" that is invisible on screen.
 *
 * Rendering the pickers is the visible half and the easy one. If what they choose does not travel
 * with the run, the screen reports a routing decision the run does not make — which is worse than
 * having no pickers, because it is wrong rather than absent.
 *
 * `null` versus an object of empty strings is the subtle part, and it is not a style choice. The
 * server merges role by role and reads a MISSING key as "keep the profile's answer", so an empty
 * slug is a request to run that role on a model named empty-string.
 */
describe("toRoleModels", () => {
  it("sends nothing at all when nothing was picked", () => {
    expect(toRoleModels(NO_OVERRIDE)).toBeNull();
  });

  it("sends only the roles that were picked", () => {
    expect(toRoleModels({ ...NO_OVERRIDE, edit: "openrouter/x" })).toEqual({
      edit: "openrouter/x",
    });
  });

  it("never sends an empty slug", () => {
    // The failure this shape exists to prevent: four keys, three of them meaning "no choice", and
    // the server dutifully trying to route `explore` to a model called "".
    const out = toRoleModels({ ...NO_OVERRIDE, plan: "openrouter/y" });

    expect(Object.values(out ?? {}).every(Boolean)).toBe(true);
    expect(Object.keys(out ?? {})).toEqual(["plan"]);
  });

  it("covers every role the bar renders", () => {
    // A role added to `ROLES` and not to `NO_OVERRIDE` would render a picker whose choice is
    // `undefined` and silently never travel — the exact defect, in a new role.
    const todos = Object.fromEntries(ROLES.map((r) => [r, `openrouter/${r}`]));

    expect(toRoleModels({ ...NO_OVERRIDE, ...todos })).toEqual(todos);
    expect(Object.keys(NO_OVERRIDE).sort()).toEqual([...ROLES].sort());
  });
});

describe("the Code screen sends what its pickers chose", () => {
  it("passes the override into the run it hands off", () => {
    // Read from source rather than driven through the UI: the hand-off fires from a "fix this"
    // control on a FAILED exchange, so reaching it in a test means staging a failure, a tool call
    // and an error — a lot of scaffolding for one property, and scaffolding that would keep
    // passing if the field were dropped from the payload.
    //
    // What can actually regress here is somebody editing the payload and not this line, and that
    // is exactly what a source assertion catches.
    const src = readFileSync(join(__dirname, "..", "Code.tsx"), "utf8");

    expect(src, "the Code screen no longer sends what its role pickers chose").toMatch(
      /roles:\s*toRoleModels\(roles\)/,
    );
  });

  it("would notice if the call were removed", () => {
    // Guarding the guard: a regex that matches nothing passes for the wrong reason.
    expect(/roles:\s*toRoleModels\(roles\)/.test("roles: toRoleModels(roles),")).toBe(true);
    expect(/roles:\s*toRoleModels\(roles\)/.test("profile_source: 'user',")).toBe(false);
  });
});
