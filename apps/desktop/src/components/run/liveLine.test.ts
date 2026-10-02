import { describe, expect, it } from "vitest";

import { liveLine } from "@/components/run/RunLauncher";
import { DICTS, LANGS } from "@/lib/i18n";

/**
 * A run waiting for its folder says so on its live feed.
 *
 * Runs now take the same lock per folder that coding turns take (2026-09-30), so a run started while
 * a conversation or another run works in that folder waits. The server says it on the feed with a
 * `folder_busy` event; a feed that dropped it would show a run that started and did nothing.
 */
const t = (key: string) => DICTS.en[key] ?? key;

describe("the run's live feed", () => {
  it("says a run is waiting for its folder", () => {
    expect(liveLine({ kind: "folder_busy", text: "server words" } as never, t as never)).toBe(
      "Waiting: another conversation or run is working in this folder",
    );
  });

  it("has the words in every language the app offers", () => {
    for (const lang of LANGS) {
      expect(DICTS[lang.code]["runs.folderBusy"], `${lang.code} is missing runs.folderBusy`).toBeTruthy();
    }
  });
});
