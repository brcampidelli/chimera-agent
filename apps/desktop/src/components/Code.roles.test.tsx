import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { Code } from "@/components/Code";
import { getFsTree, getGitStatus, getPostureFacts, getRoleModels, getRuns } from "@/lib/api";
import { emptyTree, gitStatus, postureFacts } from "@/test/code-api-mock";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", async () => (await import("@/test/code-api-mock")).makeCodeApiMock());

/**
 * Which model does which job — a control that existed and was never placed.
 *
 * `RolesBar` is 124 lines, documented, with a `compact` mode whose own comment says it is "for the
 * composer strip". Its name occurred exactly once in the entire source: its own definition. So
 * `Code.tsx` carried `const profile: Profile = "balanced"` with a comment admitting there was no
 * picker on any screen, and the words "economy" and "max" existed nowhere a user could reach.
 *
 * That is not dead code in the harmless sense. It is a capability the product has, pays to
 * maintain, translates into ten languages, and does not offer.
 */
describe("Code — the routing profile is choosable", () => {
  beforeEach(() => {
    vi.mocked(getFsTree).mockResolvedValue(emptyTree());
    vi.mocked(getGitStatus).mockResolvedValue(gitStatus());
    vi.mocked(getPostureFacts).mockResolvedValue(postureFacts());
    vi.mocked(getRuns).mockResolvedValue([]);
    vi.mocked(getRoleModels).mockResolvedValue({
      explore: "openrouter/weak",
      plan: "openrouter/top",
      edit: "openrouter/mid",
      review: "openrouter/top",
      fuse_plan: false,
      fuse_review: false,
    } as never);
  });

  it("offers all three profiles, not just the one that was hard-coded", async () => {
    renderWithProviders(<Code />);

    expect(await screen.findByRole("button", { name: "economy" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "balanced" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "max" })).toBeTruthy();
  });

  it("asks the server for the models of the profile that was picked", async () => {
    const user = userEvent.setup();
    renderWithProviders(<Code />);

    await user.click(await screen.findByRole("button", { name: "economy" }));

    // The assertion that the hard-coded constant could never satisfy.
    await waitFor(() => expect(getRoleModels).toHaveBeenCalledWith("economy"));
  });

  it("says which profile is selected, not only which one is coloured", async () => {
    const user = userEvent.setup();
    renderWithProviders(<Code />);

    await user.click(await screen.findByRole("button", { name: "max" }));

    await waitFor(() =>
      expect(screen.getByRole("button", { name: "max" }).getAttribute("aria-pressed")).toBe("true"),
    );
    expect(screen.getByRole("button", { name: "balanced" }).getAttribute("aria-pressed")).toBe(
      "false",
    );
  });
});

/**
 * The other half of the same finding: `PostureBar`, 210 lines, also rendered by nothing.
 *
 * Deleted rather than placed. The Settings screen already carries reach and approval, `PostureNote`
 * already states the resulting posture beside the session, and the composer strip has just gained
 * the profile picker. Two interactive surfaces for one setting is how the two come to disagree,
 * and the one that is easier to reach wins arguments it should not be in.
 *
 * Its three exclusive dictionary keys went with it — thirty strings across ten languages. The
 * dynamic `code.posture.reach.*` / `code.posture.approval.*` entries stayed, because Settings
 * renders those.
 */
describe("PostureBar is gone, not merely unrendered", () => {
  const SRC = join(__dirname, "..");

  function sources(dir: string, out: string[] = []): string[] {
    for (const entry of readdirSync(dir)) {
      const full = join(dir, entry);
      if (statSync(full).isDirectory()) sources(full, out);
      else if (/\.tsx?$/.test(entry)) out.push(full);
    }
    return out;
  }

  it("leaves no file and no importer behind", () => {
    const files = sources(SRC);

    expect(files.filter((f) => f.endsWith("PostureBar.tsx"))).toEqual([]);
    const importers = files.filter(
      (f) => !f.endsWith("Code.roles.test.tsx") && /\bPostureBar\b/.test(readFileSync(f, "utf8")),
    );
    expect(importers).toEqual([]);
  });

  it("keeps the keys Settings still renders", async () => {
    // The check that stops "delete the component" from becoming "delete its whole namespace".
    const { DICTS } = await import("@/lib/i18n");
    for (const [lang, dict] of Object.entries(DICTS)) {
      const table = dict as Record<string, string>;
      expect(table["code.posture.reach.workspace"], `${lang}`).toBeTruthy();
      expect(table["code.posture.title"], `${lang} kept a key nothing renders`).toBeUndefined();
    }
  });
});
