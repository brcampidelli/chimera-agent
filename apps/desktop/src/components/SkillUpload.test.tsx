/** Adding a skill of your own: what the panel says before the buttons, and what it never does.
 *
 * Adding is not consenting. An uploaded skill lands switched off and untrusted whatever its file
 * declares, and the screen has to make that legible: say it before the upload, show the row the
 * catalogue never would (an uploaded skill is in no catalogue), let the owner read the SKILL.md
 * before the switch, and answer a taken name with "replace it" rather than a dead end.
 */

import { fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { SkillUpload } from "@/components/SkillUpload";
import { getSkillBundleText, getSkillBundles, importSkill, setSkillBundleStatus } from "@/lib/api";
import { renderWithProviders } from "@/test/utils";
import type { SkillBundle } from "@/lib/types";

vi.mock("@/lib/api", () => ({
  getSkillBundles: vi.fn(),
  getSkillBundleText: vi.fn(),
  importSkill: vi.fn(),
  setSkillBundleStatus: vi.fn(),
  uninstallSkillBundle: vi.fn(),
}));

const bundles = vi.mocked(getSkillBundles);
const upload = vi.mocked(importSkill);

function bundle(over: Partial<SkillBundle> = {}): SkillBundle {
  return {
    name: "my-notes",
    description: "Keeps meeting notes in the team's format.",
    status: "pending",
    license: "",
    source: "upload: notes.md",
    ref: "sha256:00",
    installed_at: "",
    files: ["SKILL.md"],
    origin: "upload",
    provenance: "tainted",
    // The two fields the other half of P7 added to the same answer (`BundleOut`): an upload has
    // no source commit, and a switch it has not had yet is not one to reconfirm.
    committed_at: "",
    reconfirm: false,
    ...over,
  };
}

function refusal(message: string, status: number): Error {
  return Object.assign(new Error(message), { status });
}

describe("adding a skill of your own", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    bundles.mockResolvedValue([]);
  });

  it("says the skill stays off before offering to add one", async () => {
    renderWithProviders(<SkillUpload />);

    // The sentence comes before the buttons because it is the decision they do NOT make.
    expect(await screen.findByText(/leaves it switched off until you turn it on/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /add a file/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /add a folder/i })).toBeInTheDocument();
  });

  it("sends the picked file and says it stays off", async () => {
    upload.mockResolvedValue(bundle());
    renderWithProviders(<SkillUpload />);
    const file = new File(["---\nname: my-notes\n---\n"], "notes.md", { type: "text/markdown" });

    await userEvent.upload(screen.getByTestId("skill-upload-file"), file);

    await waitFor(() => expect(upload).toHaveBeenCalledWith([file], false));
    expect(await screen.findByText(/added my-notes\. it stays off/i)).toBeInTheDocument();
  });

  it("lists only what was uploaded, untrusted even when switched on", async () => {
    bundles.mockResolvedValue([
      bundle({ name: "mine-on", status: "active" }),
      bundle({ name: "from-catalogue", origin: "catalog" }),
    ]);
    renderWithProviders(<SkillUpload />);

    // A catalogue install already has its row in the catalogue; listing it twice would give one
    // skill two switches. An upload has no other row anywhere — without this one it has no switch.
    expect(await screen.findByText("mine-on")).toBeInTheDocument();
    expect(screen.queryByText("from-catalogue")).not.toBeInTheDocument();
    // Switching it on is consent to its instructions, not a change in where they came from.
    expect(screen.getByText("untrusted")).toBeInTheDocument();
  });

  it("offers to replace when the name is taken, with the same files", async () => {
    upload.mockRejectedValueOnce(refusal("a skill named 'my-notes' is already installed", 409));
    upload.mockResolvedValueOnce(bundle());
    renderWithProviders(<SkillUpload />);
    const file = new File(["x"], "notes.md", { type: "text/markdown" });

    await userEvent.upload(screen.getByTestId("skill-upload-file"), file);
    await userEvent.click(await screen.findByRole("button", { name: /replace it/i }));

    await waitFor(() => expect(upload).toHaveBeenLastCalledWith([file], true));
  });

  it("does not offer a replacement for a refusal that is not about the name", async () => {
    upload.mockRejectedValueOnce(refusal("refusing a file named '../evil.md'", 400));
    renderWithProviders(<SkillUpload />);

    await userEvent.upload(
      screen.getByTestId("skill-upload-file"),
      new File(["x"], "x.zip", { type: "application/zip" }),
    );

    // The server's sentence names the file it refused; replacing would only be refused again.
    expect(await screen.findByText(/refusing a file named/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /replace it/i })).not.toBeInTheDocument();
  });

  it("shows the SKILL.md as text before the switch means anything", async () => {
    bundles.mockResolvedValue([bundle()]);
    vi.mocked(getSkillBundleText).mockResolvedValue({
      name: "my-notes",
      text: "# Notes\n\nWrite the notes as the template says.",
      truncated: false,
    });
    renderWithProviders(<SkillUpload />);

    await userEvent.click(await screen.findByRole("button", { name: /read my-notes/i }));

    expect(await screen.findByText(/Write the notes as the template says\./)).toBeInTheDocument();
    // Plain text: the heading marker is still there, as the agent would read it.
    expect(screen.getByText(/# Notes/)).toBeInTheDocument();
    expect(vi.mocked(setSkillBundleStatus)).not.toHaveBeenCalled();
  });

  it("sends a folder with its paths and leaves the version-control litter behind", async () => {
    upload.mockResolvedValue(bundle());
    renderWithProviders(<SkillUpload />);
    const inFolder = (body: string, path: string) => {
      const file = new File([body], path.split("/").pop() as string);
      Object.defineProperty(file, "webkitRelativePath", { value: path });
      return file;
    };
    const skill = inFolder("---\nname: my-notes\n---\n", "my-notes/SKILL.md");
    const script = inFolder("print(1)", "my-notes/scripts/fill.py");
    const litter = inFolder("ref: refs/heads/main", "my-notes/.git/HEAD");

    fireEvent.change(screen.getByTestId("skill-upload-folder"), {
      target: { files: [skill, script, litter] },
    });

    await waitFor(() => expect(upload).toHaveBeenCalledWith([skill, script], false));
  });

  it("will not switch on a pending skill until its SKILL.md has been read", async () => {
    bundles.mockResolvedValue([bundle()]);
    vi.mocked(getSkillBundleText).mockResolvedValue({
      name: "my-notes",
      text: "Write the notes as the template says.",
      truncated: false,
    });
    vi.mocked(setSkillBundleStatus).mockResolvedValue(bundle({ status: "active" }));
    renderWithProviders(<SkillUpload />);

    // The screen says "it stays off until you read it and turn it on" — so the switch keeps that.
    const off = await screen.findByRole("button", { name: /^off$/i });
    expect(off).toBeDisabled();
    expect(off).toHaveAttribute("title", "Read it before turning it on.");

    await userEvent.click(screen.getByRole("button", { name: /read my-notes/i }));
    expect(await screen.findByText(/Write the notes as the template says\./)).toBeInTheDocument();
    await userEvent.keyboard("{Escape}");

    await waitFor(() => expect(screen.getByRole("button", { name: /^off$/i })).toBeEnabled());
    await userEvent.click(screen.getByRole("button", { name: /^off$/i }));
    await waitFor(() => expect(vi.mocked(setSkillBundleStatus)).toHaveBeenCalledWith("my-notes", "active"));
  });

  it("lets a skill already read and switched off go back on in one click", async () => {
    bundles.mockResolvedValue([bundle({ status: "inactive" })]);
    renderWithProviders(<SkillUpload />);

    // Off by a decision is not unread: the owner read it to switch it on the first time.
    expect(await screen.findByRole("button", { name: /^off$/i })).toBeEnabled();
  });

  it("leaves compiled Python behind — a skill whose script ran once still sends", async () => {
    upload.mockResolvedValue(bundle());
    renderWithProviders(<SkillUpload />);
    const inFolder = (body: string, path: string) => {
      const file = new File([body], path.split("/").pop() as string);
      Object.defineProperty(file, "webkitRelativePath", { value: path });
      return file;
    };
    const skill = inFolder("---\nname: my-notes\n---\n", "my-notes/SKILL.md");
    const script = inFolder("print(1)", "my-notes/scripts/fill.py");
    const cached = inFolder("\u0000", "my-notes/scripts/__pycache__/fill.cpython-312.pyc");
    const stray = inFolder("\u0000", "my-notes/scripts/old.PYC");
    // The cache directory goes whole, whatever is in it.
    const tag = inFolder("Signature: 8a477f59", "my-notes/scripts/__pycache__/CACHEDIR.TAG");

    fireEvent.change(screen.getByTestId("skill-upload-folder"), {
      target: { files: [skill, cached, script, stray, tag] },
    });

    await waitFor(() => expect(upload).toHaveBeenCalledWith([skill, script], false));
  });
});
