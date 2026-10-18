import { File, FileCode, FileCog, FileImage, FileJson, FileText, Folder, FolderOpen, Lock } from "lucide-react";
import { beforeEach, describe, expect, it } from "vitest";

import { pushRecent, readRecent, RECENT_LIMIT } from "@/lib/editorRecent";
import { fileIconFor, folderIcon } from "@/lib/fileIcons";
import { buildGitMarks, markFor, markOf } from "@/lib/gitMarks";

const row = (path: string, x: string, y: string, untracked = false) => ({ path, x, y, untracked });

describe("markOf", () => {
  it("reads a status row as one of four marks", () => {
    expect(markOf(row("a", "?", "?", true))).toBe("U");
    expect(markOf(row("a", " ", "M"))).toBe("M");
    expect(markOf(row("a", "M", " "))).toBe("M");
    expect(markOf(row("a", "A", " "))).toBe("A");
    expect(markOf(row("a", " ", "D"))).toBe("D");
    expect(markOf(row("a", "D", " "))).toBe("D");
    // Renamed and conflicted files read as modified: the tree has four marks, not twelve.
    expect(markOf(row("a", "R", " "))).toBe("M");
    expect(markOf(row("a", "U", "U"))).toBe("M");
  });

  it("calls a file added then edited 'added', and deleted-after-add 'deleted'", () => {
    expect(markOf(row("a", "A", "M"))).toBe("A");
    expect(markOf(row("a", "A", "D"))).toBe("D");
  });
});

describe("buildGitMarks / markFor", () => {
  const marks = buildGitMarks([
    row("src/app.py", " ", "M"),
    row("src/lib/new.py", "A", " "),
    row("docs/old.md", " ", "D"),
    row("notes.txt", "?", "?", true),
    row("scratch/", "?", "?", true),
  ]);

  it("marks the files themselves", () => {
    expect(markFor(marks, "src/app.py", false)).toBe("M");
    expect(markFor(marks, "src/lib/new.py", false)).toBe("A");
    expect(markFor(marks, "notes.txt", false)).toBe("U");
    expect(markFor(marks, "README.md", false)).toBeNull();
  });

  it("rolls a mark up to every folder above it, the strongest one winning", () => {
    expect(markFor(marks, "src", true)).toBe("M"); // M beats A
    expect(markFor(marks, "src/lib", true)).toBe("A");
    // A deleted file is not in the tree, but its folder still says something changed.
    expect(markFor(marks, "docs", true)).toBe("D");
    expect(markFor(marks, "other", true)).toBeNull();
  });

  it("treats everything under an untracked folder as untracked", () => {
    expect(markFor(marks, "scratch", true)).toBe("U");
    expect(markFor(marks, "scratch/a/b.txt", false)).toBe("U");
    expect(markFor(marks, "scratch/a", true)).toBe("U");
  });

  it("accepts backslash paths and says nothing without a status", () => {
    expect(markFor(marks, "src\\app.py", false)).toBe("M");
    expect(markFor(null, "src/app.py", false)).toBeNull();
  });

  it("does not mix a file name with a folder of the same name", () => {
    expect(markFor(marks, "src", false)).toBeNull();
    expect(markFor(marks, "src/app.py", true)).toBeNull();
  });
});

describe("fileIconFor", () => {
  it("picks one of the eight shapes by extension", () => {
    expect(fileIconFor("app.tsx")).toBe(FileCode);
    expect(fileIconFor("main.py")).toBe(FileCode);
    expect(fileIconFor("data.json")).toBe(FileJson);
    expect(fileIconFor("README.md")).toBe(FileText);
    expect(fileIconFor("logo.PNG")).toBe(FileImage);
    expect(fileIconFor("pyproject.toml")).toBe(FileCog);
    expect(fileIconFor("notes.xyz")).toBe(File);
    expect(fileIconFor("LICENSE")).toBe(File);
  });

  it("knows lockfiles and config files by their NAME", () => {
    // `package-lock.json` is a lock, not data; checked before the .json rule.
    expect(fileIconFor("package-lock.json")).toBe(Lock);
    expect(fileIconFor("uv.lock")).toBe(Lock);
    expect(fileIconFor("Dockerfile")).toBe(FileCog);
    expect(fileIconFor(".gitignore")).toBe(FileCog);
    expect(fileIconFor(".env.local")).toBe(FileCog);
  });

  it("opens and closes folders", () => {
    expect(folderIcon(false)).toBe(Folder);
    expect(folderIcon(true)).toBe(FolderOpen);
  });
});

describe("editorRecent", () => {
  beforeEach(() => localStorage.clear());

  it("keeps the newest first, once each, per project", () => {
    pushRecent("/a", "x.py");
    pushRecent("/a", "y.py");
    pushRecent("/a", "x.py");
    pushRecent("/b", "z.py");
    expect(readRecent("/a")).toEqual(["x.py", "y.py"]);
    expect(readRecent("/b")).toEqual(["z.py"]);
    expect(readRecent(null)).toEqual([]);
  });

  it("stops at the limit", () => {
    for (let i = 0; i < RECENT_LIMIT + 3; i++) pushRecent("/a", `f${i}.py`);
    const list = readRecent("/a");
    expect(list).toHaveLength(RECENT_LIMIT);
    expect(list[0]).toBe(`f${RECENT_LIMIT + 2}.py`);
  });

  it("reads garbage as empty instead of throwing", () => {
    localStorage.setItem("chimera:editor:recent", "{not json");
    expect(readRecent("/a")).toEqual([]);
    localStorage.setItem("chimera:editor:recent", JSON.stringify({ "/a": [1, "ok.py"] }));
    expect(readRecent("/a")).toEqual(["ok.py"]);
  });
});
