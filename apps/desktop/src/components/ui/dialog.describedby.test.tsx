import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { Dialog } from "@/components/ui/dialog";
import { I18nProvider } from "@/lib/i18n";

/**
 * A dialog with no description must not describe itself with its own title.
 *
 * Radix warns when its Content has no Description, and the previous answer to that warning was an
 * invisible one holding the TITLE. That silenced the warning and created a real defect in its
 * place: a screen reader announced the same sentence twice — once as the heading, once as the
 * description — in every dialog in the app that passes no description, which is most of them.
 *
 * Heard while walking the delete confirmation: "Excluir 1 conversa(s)? Excluir 1 conversa(s)?".
 *
 * Both directions matter. Silencing it unconditionally would take the description away from the
 * dialogs that DO have one, which is the same defect pointing the other way.
 */

function open(props: { title: string; description?: string }) {
  render(
    <I18nProvider>
      <Dialog open onOpenChange={vi.fn()} {...props}>
        <p>corpo</p>
      </Dialog>
    </I18nProvider>,
  );
  return screen.getByRole("dialog");
}

describe("Dialog — aria-describedby", () => {
  it("says nothing about a description it does not have", () => {
    const dlg = open({ title: "Excluir 1 conversa(s)?" });

    expect(dlg.getAttribute("aria-describedby")).toBeNull();
    // The title exactly once, not once visibly and once for the screen reader.
    expect(screen.getAllByText("Excluir 1 conversa(s)?")).toHaveLength(1);
  });

  it("still points at the description it does have", () => {
    // The control. Passing `aria-describedby={undefined}` unconditionally would pass the test above
    // and silently unlabel every dialog that carries a real description.
    const dlg = open({ title: "Escolher o modelo", description: "Vale para esta conversa." });
    const id = dlg.getAttribute("aria-describedby");

    expect(id).toBeTruthy();
    expect(document.getElementById(id as string)?.textContent).toBe("Vale para esta conversa.");
  });

  it("leaves no Radix warning behind", () => {
    // The warning is what the sr-only copy existed to silence, so the fix has to silence it too —
    // otherwise the next person re-adds the duplicate to quiet the console.
    const erros: unknown[] = [];
    const spy = vi.spyOn(console, "warn").mockImplementation((...a) => erros.push(a));
    const spyErr = vi.spyOn(console, "error").mockImplementation((...a) => erros.push(a));

    open({ title: "Sem descrição" });

    expect(
      erros.filter((a) => JSON.stringify(a).toLowerCase().includes("describedby")),
      "Radix is still warning about the missing description",
    ).toEqual([]);
    spy.mockRestore();
    spyErr.mockRestore();
  });
});
