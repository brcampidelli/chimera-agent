import React from "react";
import ReactDOM from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import App from "@/App";
import { I18nProvider } from "@/lib/i18n";
import "highlight.js/styles/github-dark.css";
import "@/index.css";

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

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <I18nProvider>
        <App />
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
