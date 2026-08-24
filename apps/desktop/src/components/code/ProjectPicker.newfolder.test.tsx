import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ProjectPicker } from "@/components/code/ProjectPicker";
import { browseDirs, makeDir } from "@/lib/api";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", () => ({ browseDirs: vi.fn(), makeDir: vi.fn() }));

/**
 * The picker could only SELECT, and a new project has to start somewhere.
 *
 * So starting one meant leaving the app for Explorer, coming back, and finding the folder again —
 * and the folder people actually picked was often the wrong one, because the right one did not
 * exist yet and the nearest existing parent was one click away.
 *
 * The half worth testing is what happens AFTER: creating a folder and staying in the parent leaves
 * "Use this folder" pointing at the folder the new one is inside, which is the same wrong pick
 * with an extra step in front of it.
 */

const AQUI = {
  path: "C:\\Users\\alguem\\Desktop",
  parent: "C:\\Users\\alguem",
  entries: [{ name: "loja", path: "C:\\Users\\alguem\\Desktop\\loja" }],
  capped: false,
};

describe("ProjectPicker — creating a folder", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(browseDirs).mockResolvedValue(AQUI);
  });

  async function abrirOCampo() {
    const user = userEvent.setup();
    renderWithProviders(<ProjectPicker onPick={vi.fn()} onCancel={vi.fn()} />);
    await user.click(await screen.findByRole("button", { name: /New folder|Nova pasta/i }));
    return user;
  }

  it("creates the folder inside the one being shown", async () => {
    vi.mocked(makeDir).mockResolvedValue({ path: "C:\\Users\\alguem\\Desktop\\novo", created: true });
    const user = await abrirOCampo();

    await user.type(screen.getByLabelText(/Name of the new folder|Nome da nova pasta/i), "novo");
    await user.click(screen.getByRole("button", { name: /^Create$|^Criar$/i }));

    await waitFor(() => expect(makeDir).toHaveBeenCalledWith(AQUI.path, "novo"));
  });

  it("goes into the folder it just made", async () => {
    // Otherwise "Use this folder" still means the parent, and the user picks the wrong root with
    // one more click than before — the defect this button exists to remove, reintroduced.
    vi.mocked(makeDir).mockResolvedValue({ path: "C:\\Users\\alguem\\Desktop\\novo", created: true });
    const user = await abrirOCampo();

    await user.type(screen.getByLabelText(/Name of the new folder|Nome da nova pasta/i), "novo");
    await user.click(screen.getByRole("button", { name: /^Create$|^Criar$/i }));

    await waitFor(() => expect(browseDirs).toHaveBeenCalledWith("C:\\Users\\alguem\\Desktop\\novo"));
  });

  it("says so when the name will not work", async () => {
    // The server refuses a name that cannot be a folder. A silent no-op reads as a broken button,
    // and the fix is one character away — so the reason has to be on screen.
    vi.mocked(makeDir).mockRejectedValue(new Error("invalid folder name"));
    const user = await abrirOCampo();

    await user.type(screen.getByLabelText(/Name of the new folder|Nome da nova pasta/i), "../fora");
    await user.click(screen.getByRole("button", { name: /^Create$|^Criar$/i }));

    await waitFor(() =>
      expect(screen.getByText(/will not work as a folder|não funciona como pasta/i)).toBeInTheDocument(),
    );
  });

  it("does not create anything on an empty name", async () => {
    const user = await abrirOCampo();

    expect(screen.getByRole("button", { name: /^Create$|^Criar$/i })).toBeDisabled();
    await user.type(screen.getByLabelText(/Name of the new folder|Nome da nova pasta/i), "   ");
    expect(screen.getByRole("button", { name: /^Create$|^Criar$/i })).toBeDisabled();
    expect(makeDir).not.toHaveBeenCalled();
  });

  it("keeps selecting a folder working", async () => {
    // Guarding the guard. The new control shares a row with the one this screen is FOR, and a
    // button added beside another is how the other one stops being reachable.
    const onPick = vi.fn();
    const user = userEvent.setup();
    renderWithProviders(<ProjectPicker onPick={onPick} onCancel={vi.fn()} />);

    await user.click(await screen.findByRole("button", { name: /Use this folder|Usar esta pasta/i }));

    expect(onPick).toHaveBeenCalledWith(AQUI.path);
  });
});
