import { useEffect } from "react";
import { PanelLeftClose } from "lucide-react";

import { DOCK_PANELS, panelTitleKey, type MovablePanel } from "@/components/shell/panels";
import { focusRing } from "@/components/ui/focus";
import { AgentProvider, useAgent } from "@/lib/agent-context";
import { isFloatMessage, openChannel, type FloatMessage } from "@/lib/float/protocol";
import { useT } from "@/lib/i18n";
import { cn } from "@/lib/utils";

/**
 * One panel, in a window of its own (dynamic screen, phase 7).
 *
 * It is the same page as the app, asked by `?float=` to draw one panel. It holds no layout: the main
 * window owns where everything is, and mounting the layout here would write this window's idea of it
 * over the person's. It learns the agent's state from the main window over the channel, and says when
 * it goes, so the panel is drawn in its dock again.
 */
export function FloatWindow({ panel }: { panel: MovablePanel }) {
  return (
    <AgentProvider>
      <FloatBody panel={panel} />
    </AgentProvider>
  );
}

function FloatBody({ panel }: { panel: MovablePanel }) {
  const t = useT();
  const { publish } = useAgent();
  const name = t(panelTitleKey(panel));

  useEffect(() => {
    document.title = `${name} · Chimera`;
  }, [name]);

  useEffect(() => {
    const ch = openChannel();
    if (!ch) return;
    ch.onmessage = (e: MessageEvent<unknown>) => {
      if (!isFloatMessage(e.data)) return;
      const m = e.data;
      if (m.type === "agent") publish(m.state);
      else if (m.type === "return" && m.panel === panel) window.close();
    };
    ch.postMessage({ type: "hello", panel } satisfies FloatMessage);
    const goodbye = () => ch.postMessage({ type: "closed", panel } satisfies FloatMessage);
    window.addEventListener("pagehide", goodbye);
    return () => {
      window.removeEventListener("pagehide", goodbye);
      ch.close();
    };
  }, [panel, publish]);

  function bringBack() {
    // Said first: closing may end this page before anything after it runs.
    const ch = openChannel();
    ch?.postMessage({ type: "closed", panel } satisfies FloatMessage);
    ch?.close();
    window.close();
  }

  return (
    <main aria-label={name} className="flex h-screen min-h-0 flex-col bg-background text-foreground">
      <header className="flex items-center gap-2 border-b border-hairline px-4 py-2.5">
        <h1 className="min-w-0 flex-1 truncate text-xs font-semibold uppercase tracking-wider text-muted-foreground">
          {name}
        </h1>
        <button
          type="button"
          onClick={bringBack}
          className={cn(
            "flex items-center gap-1.5 rounded-sm px-1.5 py-0.5 text-xs text-muted-foreground",
            "transition-colors duration-1 ease-out hover:bg-surface-hover hover:text-foreground",
            focusRing,
          )}
        >
          <PanelLeftClose className="h-3.5 w-3.5" aria-hidden />
          {t("layout.float.bringBack")}
        </button>
      </header>
      <div className="min-h-0 flex-1 overflow-auto px-4 py-3">{DOCK_PANELS[panel]()}</div>
    </main>
  );
}
