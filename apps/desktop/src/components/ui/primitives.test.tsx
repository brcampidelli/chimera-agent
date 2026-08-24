/**
 * The primitives, tested for the behaviour that is easy to get wrong and invisible when it is:
 * focus containment, keyboard navigation, and accessible names. None of it shows up in a
 * screenshot, and all of it is what separates a component from a div that looks like one.
 */
import { useState } from "react";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { Dialog } from "@/components/ui/dialog";
import { I18nProvider } from "@/lib/i18n";
import { Switch } from "@/components/ui/switch";
import { Tabs } from "@/components/ui/tabs";
import { ToastProvider, useToast } from "@/components/ui/toast";

describe("Switch", () => {
  it("exposes its state and its name to assistive tech", () => {
    render(<Switch checked onChange={() => {}} label="Semantic memory" />);
    const el = screen.getByRole("switch", { name: "Semantic memory" });
    expect(el).toHaveAttribute("aria-checked", "true");
  });

  it("toggles on click", async () => {
    const onChange = vi.fn();
    const user = userEvent.setup();
    render(<Switch checked={false} onChange={onChange} label="Cascade" />);
    await user.click(screen.getByRole("switch"));
    expect(onChange).toHaveBeenCalledWith(true);
  });

  it("toggles from the keyboard", async () => {
    // A real <button>, so Space and Enter work with no handler of our own — which is exactly why
    // this is a button and not a styled div.
    const onChange = vi.fn();
    const user = userEvent.setup();
    render(<Switch checked={false} onChange={onChange} label="Cascade" />);

    await user.tab(); // the switch is the only focusable thing here
    expect(screen.getByRole("switch")).toHaveFocus();

    await user.keyboard(" ");
    expect(onChange).toHaveBeenCalledWith(true);

    onChange.mockClear();
    await user.keyboard("{Enter}");
    expect(onChange).toHaveBeenCalledWith(true);
  });

  it("does not fire while disabled", async () => {
    const onChange = vi.fn();
    const user = userEvent.setup();
    render(<Switch checked={false} onChange={onChange} label="Cascade" disabled />);
    await user.click(screen.getByRole("switch"));
    expect(onChange).not.toHaveBeenCalled();
  });
});

describe("Tabs", () => {
  const ITEMS = [
    { value: "memory", label: "Memory" },
    { value: "profile", label: "Profile" },
    { value: "skills", label: "Skills" },
  ] as const;

  function Harness() {
    const [value, setValue] = useState<(typeof ITEMS)[number]["value"]>("memory");
    return <Tabs items={ITEMS} value={value} onChange={setValue} aria-label="Knowledge" />;
  }

  it("marks exactly one tab selected", () => {
    render(<Harness />);
    expect(screen.getByRole("tab", { name: "Memory" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tab", { name: "Profile" })).toHaveAttribute("aria-selected", "false");
  });

  it("uses a roving tabindex so the strip is one tab stop", () => {
    render(<Harness />);
    // The part people skip. Without it, leaving a five-tab strip costs five Tab presses.
    expect(screen.getByRole("tab", { name: "Memory" })).toHaveAttribute("tabindex", "0");
    expect(screen.getByRole("tab", { name: "Profile" })).toHaveAttribute("tabindex", "-1");
  });

  it("moves between tabs with the arrow keys, and wraps", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    await user.tab();
    expect(screen.getByRole("tab", { name: "Memory" })).toHaveFocus();

    await user.keyboard("{ArrowRight}");
    expect(screen.getByRole("tab", { name: "Profile" })).toHaveAttribute("aria-selected", "true");
    // Selection follows focus: the ring must not stay behind on the old tab.
    expect(screen.getByRole("tab", { name: "Profile" })).toHaveFocus();

    await user.keyboard("{ArrowLeft}{ArrowLeft}");
    expect(screen.getByRole("tab", { name: "Skills" })).toHaveAttribute("aria-selected", "true");
  });

  it("jumps to the ends with Home and End", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    await user.tab();
    await user.keyboard("{End}");
    expect(screen.getByRole("tab", { name: "Skills" })).toHaveAttribute("aria-selected", "true");
    await user.keyboard("{Home}");
    expect(screen.getByRole("tab", { name: "Memory" })).toHaveAttribute("aria-selected", "true");
  });
});

describe("Dialog", () => {
  // Wrapped, because the close control is named through `t()` now. It renders as an X and had
  // "Close" hardcoded, so every dialog in the app announced an English word to a screen reader
  // reading Portuguese — the app's own default. The provider is how the primitive gets its
  // language, and requiring it here is cheaper than giving the most-used primitive a fallback
  // that would quietly ship English wherever someone forgot the wrapper.
  function Harness() {
    const [open, setOpen] = useState(false);
    return (
      <I18nProvider>
        <button onClick={() => setOpen(true)}>Open</button>
        <Dialog open={open} onOpenChange={setOpen} title="Delete session">
          <button>Confirm</button>
        </Dialog>
      </I18nProvider>
    );
  }

  it("is named by its title", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    await user.click(screen.getByRole("button", { name: "Open" }));
    expect(await screen.findByRole("dialog", { name: "Delete session" })).toBeInTheDocument();
  });

  it("closes on Escape and returns focus to whatever opened it", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    const opener = screen.getByRole("button", { name: "Open" });
    await user.click(opener);
    await screen.findByRole("dialog");

    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    // The half everyone forgets: without focus restoration a keyboard user is dumped at the top of
    // the document every time they dismiss something.
    await waitFor(() => expect(opener).toHaveFocus());
  });

  it("moves focus into the dialog on open", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    await user.click(screen.getByRole("button", { name: "Open" }));
    const dialog = await screen.findByRole("dialog");
    await waitFor(() => expect(dialog.contains(document.activeElement)).toBe(true));
  });
});

describe("Toast", () => {
  function Harness() {
    const toast = useToast();
    return <button onClick={() => toast("Saved", "ok")}>Save</button>;
  }

  it("announces through a live region that exists before the message does", async () => {
    const user = userEvent.setup();
    render(
      <ToastProvider>
        <Harness />
      </ToastProvider>,
    );
    // The region is in the DOM from the start — a live region created at the same moment as its
    // content is not announced at all.
    const region = screen.getByRole("status");
    expect(region).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByText("Saved")).toBeInTheDocument();
    expect(region).toHaveTextContent("Saved");
  });

  it("can be dismissed before its timeout", async () => {
    const user = userEvent.setup();
    render(
      <ToastProvider>
        <Harness />
      </ToastProvider>,
    );
    await user.click(screen.getByRole("button", { name: "Save" }));
    await screen.findByText("Saved");
    await user.click(screen.getByRole("button", { name: "Dismiss" }));
    await waitFor(() => expect(screen.queryByText("Saved")).not.toBeInTheDocument());
  });

  it("refuses to be used outside its provider rather than silently doing nothing", () => {
    // A no-op toast is a confusing bug to chase: the code ran, and nothing appeared.
    const quiet = vi.spyOn(console, "error").mockImplementation(() => {});
    expect(() => render(<Harness />)).toThrow(/ToastProvider/);
    quiet.mockRestore();
  });
});
