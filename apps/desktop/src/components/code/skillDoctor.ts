/**
 * The `/skill-doctor` command: reading the typed line. The command is answered by the screen, not sent
 * to the agent as a turn; the server only reads the skills and returns a card (`SkillDoctorOut`).
 */

/** `/skill-doctor` alone is the command. Anything after the name is not, so the draft is sent as typed. */
export function isSkillDoctorCommand(draft: string): boolean {
  return draft.trim() === "/skill-doctor";
}
