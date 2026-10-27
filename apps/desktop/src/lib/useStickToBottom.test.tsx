import { useRef } from "react";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";

import { useStickToBottom } from "@/lib/useStickToBottom";

// jsdom does no layout, so the box's geometry is a fixture: a 200px window over `geom.height` px of
// content, scrolled by `geom.top`. The setter clamps the way a browser does, so "scroll to the end"
// means `height - client` and a write past it lands there.
const geom = { height: 1000, client: 200, top: 0 };

function Harness() {
  const ref = useRef<HTMLDivElement>(null);
  const { stuck, scrollToBottom } = useStickToBottom(ref, []);
  return (
    <>
      <div ref={ref} data-testid="box" />
      <button type="button" onClick={scrollToBottom}>
        jump
      </button>
      <span>{stuck ? "following" : "detached"}</span>
    </>
  );
}

function fixGeometry(el: HTMLElement) {
  // The smooth jump starts and goes nowhere in jsdom: the position stays where it was, which is the
  // "animation stopped short" case the per-frame catch-up exists for.
  el.scrollTo = () => {};
  Object.defineProperty(el, "scrollHeight", { configurable: true, get: () => geom.height });
  Object.defineProperty(el, "clientHeight", { configurable: true, get: () => geom.client });
  Object.defineProperty(el, "scrollTop", {
    configurable: true,
    get: () => geom.top,
    set: (v: number) => {
      geom.top = Math.max(0, Math.min(v, geom.height - geom.client));
    },
  });
}

beforeEach(() => {
  geom.height = 1000;
  geom.client = 200;
  geom.top = 0;
});

describe("useStickToBottom: the jump back down", () => {
  it("lands on the real end when the content grows after the click", async () => {
    // The case that sent the reader scrolling by hand: a large diff settles after the button is
    // pressed. A smooth scroll aimed at the height seen at the click stopped short of this.
    render(<Harness />);
    const box = screen.getByTestId("box");
    fixGeometry(box);

    fireEvent.click(screen.getByRole("button", { name: "jump" }));
    geom.height = 2600;

    await waitFor(() => expect(box.scrollTop).toBe(geom.height - geom.client));
    expect(screen.getByText("following")).toBeInTheDocument();
  });

  it("stops following once the reader scrolls back up", async () => {
    render(<Harness />);
    const box = screen.getByTestId("box");
    fixGeometry(box);

    fireEvent.click(screen.getByRole("button", { name: "jump" }));
    // The reader takes the wheel back: the position moves up and the scroll event says so.
    geom.top = 0;
    fireEvent.scroll(box);
    geom.height = 3000;

    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 80));
    });
    // Still where the reader put it: the loop must not drag them back down.
    expect(box.scrollTop).toBe(0);
    expect(screen.getByText("detached")).toBeInTheDocument();
  });
});
