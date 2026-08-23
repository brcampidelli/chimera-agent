import type { ReactElement, ReactNode } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, type RenderOptions, type RenderResult } from "@testing-library/react";
import { I18nProvider } from "@/lib/i18n";
import { RunSessionProvider } from "@/lib/run-session";
import { TooltipProvider } from "@/components/ui/tooltip";

/** The app's real provider stack (see `main.tsx`), minus StrictMode's double-render. Retries are OFF
 *  so a mocked rejection surfaces immediately instead of being retried for seconds, and each render
 *  gets a FRESH QueryClient so no cached response leaks between tests. */
export function renderWithProviders(ui: ReactElement, options?: Omit<RenderOptions, "wrapper">): RenderResult {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0, refetchOnWindowFocus: false, staleTime: 0 },
      mutations: { retry: false },
    },
  });

  function Providers({ children }: { children: ReactNode }) {
    return (
      <QueryClientProvider client={queryClient}>
        {/* The run session is part of the real stack (see `App.tsx`), and its absence is SILENT:
            `useRunSession` falls back to an inert stub, so a screen that starts runs would simply
            never start one and every assertion about a running run would fail as "not found". */}
        <I18nProvider>
          {/* Also part of the real stack (`main.tsx`), and its absence is LOUD rather than silent:
              a `Tooltip` outside it throws on render. That is worse than it sounds — the component
              under test breaks for a reason that is about the harness, so adding a tooltip to any
              screen turns a dozen unrelated test files red at once. Mirroring the app's providers
              here is what stops each of those files having to remember. */}
          <TooltipProvider>
            <RunSessionProvider>{children}</RunSessionProvider>
          </TooltipProvider>
        </I18nProvider>
      </QueryClientProvider>
    );
  }

  return render(ui, { wrapper: Providers, ...options });
}
