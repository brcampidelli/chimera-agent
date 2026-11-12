import { describe, expect, it } from "vitest";

import { isSkillDoctorCommand } from "@/components/code/skillDoctor";

/** `/skill-doctor` is answered by the screen only when it is the whole line. Anything else stays a message. */
describe("isSkillDoctorCommand", () => {
  it("accepts the bare command, with surrounding spaces", () => {
    expect(isSkillDoctorCommand("/skill-doctor")).toBe(true);
    expect(isSkillDoctorCommand("  /skill-doctor  ")).toBe(true);
  });

  it("leaves a message that merely mentions it alone", () => {
    expect(isSkillDoctorCommand("/skill-doctor please")).toBe(false);
    expect(isSkillDoctorCommand("explain /skill-doctor")).toBe(false);
    expect(isSkillDoctorCommand("/code-review")).toBe(false);
  });
});
