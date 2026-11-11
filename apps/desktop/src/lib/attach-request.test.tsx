import { act, render, screen } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it } from "vitest";

import { AttachmentTray } from "@/components/code/Attachments";
import type { Attachment } from "@/lib/api";
import { requestAddAttachments, useAddAttachmentRequest } from "@/lib/attach-request";
import { renderWithProviders } from "@/test/utils";

function Composer() {
  const [items, setItems] = useState<Attachment[]>([]);
  useAddAttachmentRequest((added) =>
    setItems((prev) => [...prev, ...added.filter((a) => !prev.some((p) => p.id === a.id))]),
  );
  return <AttachmentTray items={items} onRemove={() => {}} />;
}

describe("requestAddAttachments", () => {
  it("puts an element chip in the composer's tray, once however often it is asked", () => {
    renderWithProviders(<Composer />);
    const chip: Attachment = { id: "a1", name: "Element: <button.primary> 'Salvar'", kind: "document", chars: 90, note: "" };
    act(() => requestAddAttachments([chip]));
    act(() => requestAddAttachments([chip]));
    expect(screen.getAllByText("Element: <button.primary> 'Salvar'")).toHaveLength(1);
  });

  it("says whether anybody took them: false when no conversation is mounted", () => {
    const item: Attachment = { id: "a", name: "x", kind: "document", chars: 0, note: "" };
    render(<p>nothing here</p>);
    expect(requestAddAttachments([item])).toBe(false);
    const mounted = renderWithProviders(<Composer />);
    expect(requestAddAttachments([item])).toBe(true);
    mounted.unmount();
    expect(requestAddAttachments([item])).toBe(false);
  });
});
