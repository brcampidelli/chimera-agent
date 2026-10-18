import { describe, expect, it, vi } from "vitest";

import { RECENT_IN_PALETTE, conversationCommands, type PaletteSession } from "@/lib/paletteConversations";

const t = (key: string, params?: Record<string, string | number>) =>
  key === "palette.conversation.archivedIn" ? `Archived · ${params?.project}` : key;

function session(i: number, over: Partial<PaletteSession> = {}): PaletteSession {
  return { id: `s${i}`, title: `Task ${i}`, workspace: `/p/repo-${i}`, updated_at: i, ...over };
}

describe("the palette's conversation entries", () => {
  it("lists the newest first with its project, and only the recent ones before a search", () => {
    const many = Array.from({ length: RECENT_IN_PALETTE + 3 }, (_, i) => session(i));
    const commands = conversationCommands(many, [], t, vi.fn());
    expect(commands[0]).toMatchObject({ label: `Task ${many.length - 1}`, group: `repo-${many.length - 1}` });
    expect(commands.filter((c) => !c.searchOnly)).toHaveLength(RECENT_IN_PALETTE);
    expect(commands).toHaveLength(many.length);
  });

  it("names the project from the repository a worktree belongs to, and the untitled and default cases", () => {
    const [worktree, untitled] = conversationCommands(
      [
        session(2, { workspace: "/tmp/wt/w-1", project: "/p/agent", project_name: "Agent AI" }),
        session(1, { title: "", workspace: "" }),
      ],
      [],
      t,
      vi.fn(),
    );
    expect(worktree.group).toBe("Agent AI");
    expect(untitled).toMatchObject({ label: "code.sessions.untitled", group: "code.sessions.defaultProject" });
  });

  it("puts the archived ones last, search-only and marked, without listing one twice", () => {
    const open = vi.fn();
    const old = session(9, { title: "Old" });
    const commands = conversationCommands([session(1), old], [old], t, open);
    expect(commands.map((c) => c.id)).toEqual(["conversation-s1", "archived-s9"]);
    expect(commands[1]).toMatchObject({ searchOnly: true, group: "Archived · repo-9" });
    commands[1].run();
    expect(open).toHaveBeenCalledWith(old);
  });
});
