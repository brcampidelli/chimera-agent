import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import { DICTS } from "@/lib/i18n";

/**
 * Every key in the dictionary has to be reachable from a screen.
 *
 * A key nobody renders is not free. It is ten translations to keep in step, ten more lines a
 * translator reads and weighs, and — worse — a sentence that looks like a promise the product makes
 * while nothing shows it to anyone. Two security notes lived in all ten languages and in no `.tsx`
 * file; five `code.plan*` keys described a preview screen that was deliberately removed and whose
 * strings stayed behind.
 *
 * The hard part is that many keys are built at the call site — `` t(`tools.desc.${tool.name}`) `` —
 * so a naive literal search would call 175 live keys dead. Those prefixes are enumerated below,
 * each one read out of the source rather than guessed, and a key under one of them counts as
 * reachable. Adding a prefix here is the price of adding a dynamic lookup, which is the right
 * price: a dynamic key is exactly the kind nobody notices going stale.
 */

const SRC = join(__dirname, "..");

/** Prefixes assembled at the call site. Each is a real `` t(`prefix.${x}`) `` in the source. */
const DYNAMIC = [
  "activity.",
  "catalog.portability.",
  "catalog.reason.",
  "catalog.state.",
  "fusion.aggregation.",
  "code.chat.example.",
  "code.posture.approval.",
  "code.posture.reach.",
  "code.posture.saysPause.",
  "code.posture.saysShell.",
  "code.provider.note.",
  "code.roles.profile.",
  "crew.approach.",
  "crew.status.",
  "fusion.role.",
  // `` t(`governance.sandbox.why.${data.reason_code}`) `` — the Security screen says WHY no
  // kernel boundary applies, in the reader's language rather than relaying the server's
  // English. The codes come from `chimera.sandbox.os_sandbox.UnavailableCode` plus the API
  // layer's own `no_container`.
  "governance.sandbox.why.",
  "lifecycle.stage.",
  "runs.reqs.kind.",
  "lifecycle.status.",
  "model.reason.",
  "nav.",
  "orch.stage.",
  "orch.worker.",
  "runs.evidence.",
  "server.err",
  "settings.ollama.reason.",
  "settings.value.",
  "skills.stage.",
  "skills.status.",
  "tasks.column.",
  "tools.desc.",
  "tools.tag.",
];

function sources(dir: string, out: string[] = []): string[] {
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) {
      sources(full, out);
    } else if (/\.tsx?$/.test(entry) && !entry.endsWith("i18n.tsx")) {
      out.push(full);
    }
  }
  return out;
}

const CODE = sources(SRC)
  .map((file) => readFileSync(file, "utf8"))
  .join("\n");

describe("the dictionary", () => {
  it("has no key that nothing can render", () => {
    const dead = Object.keys(DICTS.en).filter(
      (key) =>
        !DYNAMIC.some((prefix) => key.startsWith(prefix)) &&
        !CODE.includes(`"${key}"`) &&
        !CODE.includes(`'${key}'`) &&
        !CODE.includes(`\`${key}\``),
    );

    expect(dead, `keys in ten languages that no screen renders:\n${dead.join("\n")}`).toEqual([]);
  });

  it("still sees most keys as reachable", () => {
    // A check that has quietly started calling everything dynamic reports "no dead keys" for the
    // same reason a clean dictionary does. If this number collapses, the prefix list has eaten the
    // test rather than the dictionary having got smaller.
    const total = Object.keys(DICTS.en).length;
    const dynamic = Object.keys(DICTS.en).filter((key) =>
      DYNAMIC.some((prefix) => key.startsWith(prefix)),
    ).length;

    expect(total).toBeGreaterThan(500);
    expect(dynamic / total).toBeLessThan(0.35);
  });

  it("names only prefixes that are really assembled somewhere", () => {
    // A stale prefix is an exemption nobody granted on purpose: it hides every key under it.
    const unused = DYNAMIC.filter((prefix) => !CODE.includes(`\`${prefix}\${`));

    expect(unused, `prefixes exempted here but not built anywhere:\n${unused.join("\n")}`).toEqual(
      [],
    );
  });
});
