import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { Profile } from "@/components/Profile";
import { editMemory, getInstructions, getMemoryLayers, getMemoryProfile, listCodeProjects, putInstructions } from "@/lib/api";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", () => ({
  editMemory: vi.fn(),
  getInstructions: vi.fn(),
  getMemoryLayers: vi.fn(),
  getMemoryProfile: vi.fn(),
  listCodeProjects: vi.fn(),
  putInstructions: vi.fn(),
}));

beforeEach(() => {
  localStorage.setItem("chimera.lang", "en");
  vi.clearAllMocks();
  vi.mocked(getInstructions).mockResolvedValue({ name: "", language: "English", instructions: "" } as never);
  vi.mocked(getMemoryLayers).mockResolvedValue({ total: 1, clean: 1, tainted: 0, layers: [], by_source: [], semantic_embeddings_enabled: false } as never);
  vi.mocked(getMemoryProfile).mockResolvedValue({ profile: "Prefers concise replies", persona: [{ id: "p1", content: "Prefers concise replies", source: "user", provenance: "verified" }] } as never);
  vi.mocked(listCodeProjects).mockResolvedValue([
    { path: "/work/atlas", alias: "Atlas", pinned: false, hidden: false, shell_granted: false },
    { path: "/work/secret", alias: "Secret", pinned: false, hidden: true, shell_granted: false },
  ] as never);
});

describe("Profile", () => {
  it("shows AgentIdentity language and omits a duplicate interface language when they match", async () => {
    renderWithProviders(<Profile />);

    await screen.findByRole("heading", { name: "Your agent" });
    expect(screen.getAllByText("Answer in")).toHaveLength(1);
    expect(screen.getByRole("textbox", { name: "Answer in" })).toHaveValue("English");
    expect(screen.queryByText("Language: English")).not.toBeInTheDocument();
  });

  it("shows the interface language alongside a different AgentIdentity language", async () => {
    vi.mocked(getInstructions).mockResolvedValue({ name: "", language: "Português", instructions: "" } as never);
    renderWithProviders(<Profile />);

    await screen.findByRole("heading", { name: "Your agent" });
    expect(screen.getByRole("textbox", { name: "Answer in" })).toHaveValue("Português");
    expect(screen.getByText("Language: English")).toBeInTheDocument();
  });

  it("does not offer a global response-style editor in the profile", async () => {
    renderWithProviders(<Profile />);

    await screen.findByText("Choose style in the composer");
    expect(screen.queryByRole("combobox", { name: /response style/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("textbox", { name: /response style/i })).not.toBeInTheDocument();
  });

  it("edits answer language through AgentIdentity and shows contextual style without a global setting", async () => {
    const user = userEvent.setup();
    vi.mocked(putInstructions).mockResolvedValue({ name: "", language: "Português", instructions: "" } as never);
    renderWithProviders(<Profile />);

    const language = await screen.findByRole("textbox", { name: "Answer in" });
    await user.clear(language);
    await user.type(language, "Português");
    const saveButton = language.closest("div.flex")?.querySelector("button");
    expect(saveButton).not.toBeNull();
    await user.click(saveButton!);

    await waitFor(() => expect(putInstructions).toHaveBeenCalledWith({ name: "", language: "Português", instructions: "" }, expect.anything()));
    expect(screen.getByText(/Chosen separately for each conversation/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Choose style in the composer" })).toHaveAttribute("href", "#/code");
  });

  it("lists only registered, visible sidebar projects and editable persona facts", async () => {
    renderWithProviders(<Profile />);

    expect(await screen.findByText("Registered projects")).toBeInTheDocument();
    expect(screen.getByText("Atlas")).toBeInTheDocument();
    expect(screen.queryByText("Secret")).not.toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "Persona facts p1" })).toHaveValue("Prefers concise replies");
  });

  it("saves an edited persona fact through the existing memory route", async () => {
    const user = userEvent.setup();
    renderWithProviders(<Profile />);

    const fact = await screen.findByRole("textbox", { name: "Persona facts p1" });
    await user.clear(fact);
    await user.type(fact, "Prefers detailed replies");
    const row = fact.closest("li");
    expect(row).not.toBeNull();
    await user.click(within(row!).getByRole("button", { name: "Save" }));

    await waitFor(() => expect(editMemory).toHaveBeenCalledWith("p1", "Prefers detailed replies"));
  });
});
