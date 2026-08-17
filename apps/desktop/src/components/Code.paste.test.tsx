import { fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Code } from "@/components/Code";
import {
  getFsTree,
  getGitStatus,
  getPostureFacts,
  getRuns,
  getVisionSupport,
  streamCodeTurn,
  uploadAttachment,
} from "@/lib/api";
import { emptyTree, gitStatus, postureFacts, scriptTurn } from "@/test/code-api-mock";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", async () => (await import("@/test/code-api-mock")).makeCodeApiMock());

const SHOT = { id: "p1", name: "screenshot.png", kind: "image", chars: 0, note: "" };

/**
 * Taking a screenshot and pressing Ctrl+V is how people show a program what is wrong with it.
 *
 * The composer had a paperclip and nothing else: a pasted image was dropped on the floor, and the
 * only route in was Save As followed by the file dialog. Dropping a file onto the textarea was
 * worse than unsupported — without a `preventDefault` the browser navigates to the dropped file,
 * which takes the whole conversation with it.
 */
describe("Code — a file that arrives without the file dialog", () => {
  beforeEach(() => {
    vi.mocked(getFsTree).mockResolvedValue(emptyTree());
    vi.mocked(getGitStatus).mockResolvedValue(gitStatus());
    vi.mocked(getRuns).mockResolvedValue([]);
    vi.mocked(getPostureFacts).mockResolvedValue(postureFacts());
    vi.mocked(getVisionSupport).mockResolvedValue({ supported: true, model: "m", reason: "" } as never);
    vi.mocked(streamCodeTurn).mockImplementation(scriptTurn());
    vi.mocked(uploadAttachment).mockReset();
  });

  async function composer() {
    renderWithProviders(<Code />);
    return await screen.findByPlaceholderText(/^Ask about this code/);
  }

  /** A paste event carrying a file, in the shape the browser really delivers it.
   *
   *  `fireEvent` rather than `userEvent.paste`: user-event builds its own DataTransfer and will not
   *  carry a synthetic `items` list, which is precisely the field a pasted image arrives in. */
  function pasteFile(box: HTMLElement, file: File) {
    fireEvent.paste(box, {
      clipboardData: {
        getData: () => "",
        items: [{ kind: "file", type: file.type, getAsFile: () => file }],
        files: [],
      },
    });
  }

  it("uploads an image pasted into the composer", async () => {
    vi.mocked(uploadAttachment).mockResolvedValue(SHOT as never);
    const box = await composer();
    const file = new File(["png"], "screenshot.png", { type: "image/png" });

    // `items`, not `files`: a pasted image lands there, and on some platforms `files` is empty for
    // it. Testing through the shape the browser actually delivers is the point.
    pasteFile(box, file);

    await waitFor(() => expect(uploadAttachment).toHaveBeenCalledWith(file));
    expect(await screen.findByText("screenshot.png")).toBeInTheDocument();
    expect(box).toHaveValue("");
  });

  it("still pastes text as text", async () => {
    // The guard on the guard: intercepting every paste would break the ordinary one, which is the
    // thing this box is for. Only a paste that actually carries a file is taken.
    const box = await composer();
    await userEvent.click(box);
    await userEvent.paste("fix the failing test");

    expect(box).toHaveValue("fix the failing test");
    expect(uploadAttachment).not.toHaveBeenCalled();
  });

  it("uploads a dropped file, and claims the dragover so the browser cannot navigate", async () => {
    // The `preventDefault` on dragOver is the load-bearing half. Without it the browser treats the
    // drop as a navigation and opens the file — taking the whole conversation with it, which is a
    // worse outcome than not supporting drops at all. Asserting `defaultPrevented` is the only way
    // to observe that from a test; nothing here can watch a navigation that did not happen.
    vi.mocked(uploadAttachment).mockResolvedValue(SHOT as never);
    const box = await composer();
    const file = new File(["png"], "screenshot.png", { type: "image/png" });
    const dataTransfer = { types: ["Files"], files: [file] };

    const over = new Event("dragover", { bubbles: true, cancelable: true });
    Object.defineProperty(over, "dataTransfer", { value: dataTransfer });
    fireEvent(box, over);
    expect(over.defaultPrevented).toBe(true);

    fireEvent.drop(box, { dataTransfer });
    await waitFor(() => expect(uploadAttachment).toHaveBeenCalledWith(file));
  });

  it("says so when a pasted file could not be uploaded", async () => {
    // Silence here leaves someone waiting for a screenshot that never arrived. The paperclip
    // reports its own failures beside itself; a clipboard file has no button to stand next to.
    vi.mocked(uploadAttachment).mockRejectedValue(new Error("413"));
    const box = await composer();
    const file = new File(["png"], "huge.png", { type: "image/png" });

    pasteFile(box, file);

    expect(await screen.findByText(/huge\.png could not be attached/)).toBeInTheDocument();
  });
});
