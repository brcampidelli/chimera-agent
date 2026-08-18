import { describe, expect, it } from "vitest";
import { exchangeToMarkdown, reconcile, toMarkdown, transcriptFilename } from "@/lib/transcript";

/**
 * A record of what an agent did to a repository could not leave the window it happened in.
 *
 * Until this, the only clipboard call in the whole app copied a `pip install` line — so a
 * conversation that edited someone's code could not be quoted in a bug report, pasted into a
 * review, or saved anywhere at all.
 *
 * The tests that matter here are not "does it produce Markdown". They are the three ways an export
 * can be quietly WRONG: missing turns, a fence that swallows the rest of the document, and output
 * the server clipped being presented as complete.
 */
describe("transcript", () => {
  const meta = { exportedAt: "2026-08-18T04:00:00.000Z" };

  it("keeps both sides of every exchange", () => {
    const md = toMarkdown([{ you: "fix the test", answer: "done" }], meta);
    expect(md).toContain("fix the test");
    expect(md).toContain("done");
    expect(md).toContain("Exchanges: 1");
  });

  it("marks an observation the server clipped", () => {
    // The server keeps the head and the tail of 400 characters. A transcript that presents that as
    // the whole output is how somebody concludes a suite passed from text cut before the failures —
    // and unlike the live UI, a file gets read later, by someone who was not there.
    const md = toMarkdown(
      [
        {
          you: "run them",
          answer: "ran",
          tools: [
            {
              name: "run_shell",
              arguments: { cmd: "pytest" },
              ok: false,
              observation: "start\n… (+3200 chars)\nFAILED test_x.py",
            },
          ],
        },
      ],
      meta,
    );
    expect(md).toContain("clipped");
    expect(md).toContain("FAILED test_x.py");
  });

  it("does not mark output that was never clipped", () => {
    const md = toMarkdown(
      [{ you: "q", answer: "a", tools: [{ name: "read_file", arguments: {}, ok: true, observation: "x = 1" }] }],
      meta,
    );
    expect(md).not.toContain("clipped");
  });

  it("survives an answer that contains a code fence", () => {
    // A body with ``` inside would end the fence early, and the REST of the transcript would render
    // as prose — including, in the worst case, tool output continuing into what looks like the
    // user's next message. Longer fences are the standard escape.
    const observation = "```\nnested\n```";
    const md = toMarkdown(
      [{ you: "q", answer: "a", tools: [{ name: "read_file", arguments: {}, ok: true, observation }] }],
      meta,
    );
    expect(md).toContain("````");
    // The nested fence must still be intact inside the outer one.
    expect(md).toContain("nested");
  });

  it("prefers the stored session when it holds turns this window never saw", () => {
    // A session replayed after a reload, or continued in a second window, is longer than what this
    // component has. Exporting from memory would hand somebody a transcript missing the middle —
    // worse than no transcript, because it will be believed.
    const memory = [{ you: "c", answer: "3" }];
    const stored = [
      { you: "a", answer: "1" },
      { you: "b", answer: "2" },
      { you: "c", answer: "3" },
    ];
    const out = reconcile(memory, stored);
    expect(out.source).toBe("stored");
    expect(out.recovered).toBe(2);
    expect(out.exchanges).toHaveLength(3);
  });

  it("keeps what is in memory when the stored copy is shorter or missing", () => {
    // The live turn is not in the stored copy until it finishes, so "stored always wins" would drop
    // the exchange the user is looking at.
    const memory = [{ you: "a", answer: "1" }, { you: "b", answer: "2" }];
    expect(reconcile(memory, [{ you: "a", answer: "1" }]).source).toBe("memory");
    expect(reconcile(memory, null).source).toBe("memory");
    expect(reconcile(memory, null).recovered).toBe(0);
  });

  it("produces a filename that saves on Windows", () => {
    // Colons are legal on POSIX and forbidden on Windows, and an ISO timestamp is full of them. A
    // download that silently fails to save is the kind of bug nobody reports — they just stop using
    // the feature.
    const name = transcriptFilename("2026-08-18T04:00:00.000Z");
    expect(name).not.toMatch(/[:*?"<>|]/);
    expect(name.endsWith(".md")).toBe(true);
  });

  it("renders one exchange on its own, for the copy button", () => {
    const one = exchangeToMarkdown({ you: "q", answer: "a" });
    expect(one).toContain("### You");
    expect(one).toContain("### Chimera");
    expect(one).not.toContain("# Chimera conversation");
  });

  it("says so rather than lying when an answer is empty", () => {
    // An empty answer next to a heading reads as "it said nothing worth keeping". It usually means
    // the turn failed, and the export should not quietly agree that nothing happened.
    expect(exchangeToMarkdown({ you: "q", answer: "" })).toContain("(no answer)");
  });
});
