import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { SkillDoctorCard } from "@/components/code/SkillDoctorCard";
import type { SkillDoctor } from "@/lib/types";
import { renderWithProviders } from "@/test/utils";

/**
 * The `/skill-doctor` card. Pinned: largest cost first as the server sends it; a bundle with no record of
 * use reads "No record" and never "Unused"; a card with a measured zero is suggested for switching off;
 * the per-task line appears only when card reading is on; a card on probation is never suggested for
 * switching off; and "Open Skills" leads to the Skills screen and does not switch anything off.
 */
const REPORT: SkillDoctor = {
  skills: [
    { name: "long-skill", kind: "bundle", origin: "plugin", tokens: 120, injected_runs: null, suggestion: "no_record", every_turn: true },
    { name: "idle-card", kind: "card", origin: "learned", tokens: 40, injected_runs: 0, suggestion: "disable", every_turn: false },
  ],
  tokens_per_turn: 150,
  cards_tokens_per_task_max: 40,
  cards_read: true,
  cards_k: 3,
  usage_days: null,
};

describe("SkillDoctorCard", () => {
  it("shows each skill with its cost, its origin and an honest use count", () => {
    renderWithProviders(<SkillDoctorCard report={REPORT} onOpenSkills={() => {}} />);
    expect(screen.getByText("long-skill")).toBeInTheDocument();
    expect(screen.getByText("Plugin")).toBeInTheDocument();
    expect(screen.getByText("No record")).toBeInTheDocument();
    expect(screen.getByText("Injected into 0 run(s)")).toBeInTheDocument();
    expect(screen.getByText("Credited in 0 runs: consider switching off")).toBeInTheDocument();
  });

  it("never calls a skill idle when nothing counted it: no record is not zero", () => {
    renderWithProviders(<SkillDoctorCard report={{ ...REPORT, skills: [REPORT.skills[0]] }} onOpenSkills={() => {}} />);
    expect(screen.queryByText(/Credited in 0 runs/)).not.toBeInTheDocument();
    expect(screen.getByText("Use not recorded: decide yourself")).toBeInTheDocument();
  });

  it("does not suggest switching off a card on probation, even with no run counted", () => {
    const probation = { ...REPORT.skills[1], name: "young-card", suggestion: "probation", injected_runs: 0 };
    renderWithProviders(<SkillDoctorCard report={{ ...REPORT, skills: [probation] }} onOpenSkills={() => {}} />);
    expect(screen.getByText("On probation: the lifecycle decides")).toBeInTheDocument();
    expect(screen.queryByText(/consider switching off/)).not.toBeInTheDocument();
  });

  it("shows the per-task line only when card reading is on", () => {
    const { unmount } = renderWithProviders(<SkillDoctorCard report={REPORT} onOpenSkills={() => {}} />);
    expect(screen.getByText(/can add up to/)).toBeInTheDocument();
    unmount();
    renderWithProviders(
      <SkillDoctorCard report={{ ...REPORT, cards_read: false, cards_k: 0 }} onOpenSkills={() => {}} />,
    );
    expect(screen.queryByText(/can add up to/)).not.toBeInTheDocument();
  });

  it("says so when no skill is switched on", () => {
    renderWithProviders(<SkillDoctorCard report={{ ...REPORT, skills: [] }} onOpenSkills={() => {}} />);
    expect(screen.getByText("No skills are switched on for this folder.")).toBeInTheDocument();
  });

  it("opens the Skills screen when asked, and switches nothing off", async () => {
    const onOpenSkills = vi.fn();
    renderWithProviders(<SkillDoctorCard report={REPORT} onOpenSkills={onOpenSkills} />);
    await userEvent.click(screen.getByRole("button", { name: "Open Skills" }));
    expect(onOpenSkills).toHaveBeenCalledTimes(1);
  });
});
