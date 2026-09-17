import React from "react";
import ReactDOM from "react-dom/client";

import { GuestPage } from "@/guest/GuestPage";
import { I18nProvider } from "@/lib/i18n";
import "highlight.js/styles/github-dark.css";
import "@/index.css";
import "@/styles/motion.css";

// The guest page's own entry: no query client, no tooltips, no toasts, no service worker — a
// person who opened a link to one conversation gets that conversation and the app's look, and
// nothing that belongs to the owner's app.
ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <I18nProvider>
      <GuestPage />
    </I18nProvider>
  </React.StrictMode>,
);
