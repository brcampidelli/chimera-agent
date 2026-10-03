import React from "react";
import ReactDOM from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import App from "@/App";
import { I18nProvider } from "@/lib/i18n";
import { TooltipProvider } from "@/components/ui/tooltip";
import { ToastProvider } from "@/components/ui/toast";
import { LayoutProvider } from "@/lib/layout/context";
import { LayoutServerSync } from "@/lib/layout/sync";
import { FloatWindow } from "@/components/shell/FloatWindow";
import { ConversationWindow } from "@/components/code/ConversationWindow";
import { conversationFrom, floatPanelFrom } from "@/lib/float/protocol";
import { installFocusBeacon } from "@/lib/notify";
import "highlight.js/styles/github-dark.css";
import "@/index.css";
// After index.css: motion.css consumes the --dur-*/--ease-* tokens declared there, and its
// reduced-motion overrides must win over anything a component sets.
import "@/styles/motion.css";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      refetchOnWindowFocus: false,
      // Keep a screen's data "fresh" for 30s so revisiting it doesn't refetch (and re-spin). The old
      // 5s meant any tab you came back to after a few seconds fired a fresh request.
      staleTime: 30_000,
      // A booting backend — especially the frozen desktop sidecar, which unpacks + imports the whole
      // agent stack — refuses connections for the first few seconds. React Query's default backoff is
      // 1s → 2s → 4s, so the very first screen would spin ~7s waiting that out. Retry quickly with a
      // bounded delay instead, so the startup spinner clears in ~1–2s.
      retry: 6,
      retryDelay: (attempt) => Math.min(250 * 2 ** attempt, 1500),
    },
  },
});

// `?float=<panel>` asks this page to draw one panel in a window of its own; anything else is the app.
const floating = floatPanelFrom(window.location.search);
// `?conversation=<id>` asks it to draw one conversation, so two can be worked at once.
const conversation = conversationFrom(window.location.search);

// Every window, whatever it draws: a notification is held back while ANY Chimera window has focus,
// and each window learns that from the one that has it (see `windowIsWatched`).
installFocusBeacon();

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <I18nProvider>
        {/* One TooltipProvider for the app so tooltips share a delay group: after the first
            opens, moving along the rail shows the rest instantly instead of re-waiting. */}
        <TooltipProvider>
          <ToastProvider>
            {/* The screen's layout (what is hidden, minimised, moved, how wide). Above App so the
                command palette, which App builds, can restore it. */}
            {conversation ? (
              // One conversation in a window of its own. It reads the layout and writes none of it.
              <ConversationWindow sessionId={conversation} />
            ) : floating ? (
              // One panel in a window of its own (phase 7). No layout here: the main window owns it.
              <FloatWindow panel={floating} />
            ) : (
              <LayoutProvider>
                {/* Keeps the layout on the server too, so a reinstall does not lose it (phase 6). */}
                <LayoutServerSync />
                <App />
              </LayoutProvider>
            )}
          </ToastProvider>
        </TooltipProvider>
      </I18nProvider>
    </QueryClientProvider>
  </React.StrictMode>,
);

// Register the service worker only in the built app (not under the Vite dev server, where a caching
// SW would fight HMR). This is what makes the app installable to the desktop as a PWA.
if (import.meta.env.PROD && "serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/sw.js").catch(() => {
      /* installability is a progressive enhancement — a failed SW must not break the app */
    });
  });
}
