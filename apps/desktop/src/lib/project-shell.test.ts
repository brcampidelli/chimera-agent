import { beforeEach, describe, expect, it, vi } from "vitest";

import { SHELL_KEY, setShellAllowed, shellAllowed } from "@/lib/project-shell";

/**
 * Which projects the agent may run commands in.
 *
 * The lever for this was global — one setting covering every folder — so the honest choices were
 * "no project may run anything" and "every project may". Running the tests and the installs is what
 * separates writing files from building something that works, and it is a decision people make per
 * project, not once for their whole machine.
 *
 * This store is a convenience, not the enforcement: `assemble_registry` opens the gate only when
 * the reach mounts the shell tools AND the request asks, and `CHIMERA_HOST_EXEC=deny` refuses
 * regardless. Nothing here can grant anything — it can only remember, or forget.
 */
describe("shell permission per project", () => {
  beforeEach(() => localStorage.clear());

  it("says no about a project nobody granted", () => {
    expect(shellAllowed("/projects/a")).toBe(false);
  });

  it("remembers a grant, for that project only", () => {
    setShellAllowed("/projects/a", true);

    expect(shellAllowed("/projects/a")).toBe(true);
    // The whole point: granting for one folder is not granting for the next one opened.
    expect(shellAllowed("/projects/b")).toBe(false);
  });

  it("revokes without disturbing the others", () => {
    setShellAllowed("/projects/a", true);
    setShellAllowed("/projects/b", true);

    setShellAllowed("/projects/a", false);

    expect(shellAllowed("/projects/a")).toBe(false);
    expect(shellAllowed("/projects/b")).toBe(true);
  });

  it("says no when there is no project", () => {
    // "" is what `readWorkspace` returns when nothing was chosen, and there is no "this project"
    // to grant anything to.
    setShellAllowed("", true);
    expect(shellAllowed("")).toBe(false);
  });

  it("says no when the store holds nonsense", () => {
    // A corrupt store is not a grant. This fails towards refusing, which is the only direction a
    // permission may fail in.
    localStorage.setItem(SHELL_KEY, "{not json");
    expect(shellAllowed("/projects/a")).toBe(false);

    localStorage.setItem(SHELL_KEY, '{"a": true}');
    expect(shellAllowed("/projects/a")).toBe(false);
  });

  it("survives storage being unavailable", () => {
    // Private mode, a sandboxed webview. The grant will not last the restart, which again fails
    // towards refusing rather than throwing in the middle of a click.
    const real = localStorage.setItem;
    localStorage.setItem = vi.fn(() => {
      throw new Error("nope");
    });
    try {
      expect(() => setShellAllowed("/projects/a", true)).not.toThrow();
    } finally {
      localStorage.setItem = real;
    }
  });

  it("clears the key entirely when the last grant goes", () => {
    setShellAllowed("/projects/a", true);
    setShellAllowed("/projects/a", false);

    expect(localStorage.getItem(SHELL_KEY)).toBeNull();
  });
});
