import type { Approval, PostureAxes, Reach } from "@/lib/api";

/**
 * The permission modes the Code screen offers, each one a standing answer to "when does the agent
 * stop and ask me?", expressed on the two axes the server already enforces (`chimera/api/posture.py`).
 *
 * `bypass` is not a posture. It is the owner's "Ignore permissions" switch, which the server reads
 * from the conversation or the project; it is listed here so one selector can show it, and
 * {@link modeTurn} answers for it with the base posture only.
 */
export const PERMISSION_MODES = ["manual", "accept_edits", "plan", "auto", "dont_ask", "bypass"] as const;
export type PermissionMode = (typeof PERMISSION_MODES)[number];

/** What one turn is sent with, for a mode: the posture and whether the plan gate is forced on. */
export interface ModeTurn {
  posture: Required<PostureAxes>;
  planGate: boolean;
}

/** The configured standing decision a mode is read against: the reach (already raised by a project
 *  grant where that applies) and the approval from Settings. */
export interface ModeBase {
  reach: Reach;
  approval: Approval;
}

/**
 * The posture one turn runs under, for a mode. Every answer is one of the two axes the server knows,
 * plus `commands`, the one fact the server needed to express "edit freely, ask before commands".
 *
 * - **Manual**: the configured reach with approval `always`. On a Code turn that value changes nothing yet (only `suspicious` narrows on taint); Manual adds no question of its own.
 * - **Accept edits**: the workspace may be edited without asking (`never`), and every command asks
 *   first (`commands: "asks"`). The reach is `workspace_shell` so commands exist at all; the server
 *   still removes them unless the project granted commands, and never raises a `read_only` owner's
 *   setting.
 * - **Plan**: the configured posture (approval never wider than Auto's `suspicious`), with the plan gate forced on, so the turn stops on its plan
 *   before it touches anything and runs only once the plan is approved.
 * - **Auto**: pauses only when the run read something suspicious (`suspicious`).
 * - **Don't ask**: the configured reach, nothing held (`never`). Hard blocks, the denylist and governance
 *   still apply; only the questions go. This is the posture a configured `never` always had.
 * - **Bypass**: the configured posture; the conversation's or project's switch answers the questions.
 */
export function modeTurn(mode: Exclude<PermissionMode, "bypass">, base: ModeBase): ModeTurn {
  switch (mode) {
    case "manual":
      return { posture: { reach: base.reach, approval: "always", commands: "granted" }, planGate: false };
    case "accept_edits":
      return {
        posture: {
          reach: base.reach === "read_only" ? "read_only" : "workspace_shell",
          approval: "never",
          commands: "asks",
        },
        planGate: false,
      };
    case "plan":
      return {
        posture: { reach: base.reach, approval: base.approval === "never" ? "suspicious" : base.approval, commands: "granted" },
        planGate: true,
      };
    case "auto":
      return { posture: { reach: base.reach, approval: "suspicious", commands: "granted" }, planGate: false };
    case "dont_ask":
      return { posture: { reach: base.reach, approval: "never", commands: "granted" }, planGate: false };
  }
}

/** The posture a turn runs under when the mode is bypass: the configured one, unchanged. */
export function bypassTurn(base: ModeBase): ModeTurn {
  return { posture: { reach: base.reach, approval: base.approval, commands: "granted" }, planGate: false };
}

/**
 * The mode a Settings approval reads as, when the conversation has not chosen one. The default
 * follows Settings, so a person who never opens the selector keeps the posture they configured:
 * `always` is Manual, `suspicious` is Auto, and `never` is Don't ask (the same posture, named).
 */
export function modeForApproval(approval: Approval): Exclude<PermissionMode, "bypass"> {
  if (approval === "always") return "manual";
  if (approval === "never") return "dont_ask";
  return "auto";
}

/** Whether a stored value is a mode a conversation may keep. Bypass is the owner's switch, not a mode. */
export function isStorableMode(value: string): value is Exclude<PermissionMode, "bypass"> {
  return (PERMISSION_MODES as readonly string[]).includes(value) && value !== "bypass";
}
