"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ThemeProvider } from "next-themes";
import { useEffect, useState, type ReactNode } from "react";
import { Toaster } from "sonner";

import { TooltipProvider } from "@/components/ui/tooltip";
import { startBrowserTracing } from "@/lib/telemetry";

/**
 * Everything that must wrap the whole tree, in one place.
 *
 * - Theme: `class` strategy on <html>, follows prefers-color-scheme until the
 *   user chooses, then persists the choice (next-themes handles the no-flash
 *   inline script).
 * - Query cache: server state with sane defaults — no refetch storms on focus,
 *   short staleness so history/binders feel live without hammering the API.
 */
export function AppProviders({ children }: { children: ReactNode }) {
  // Tracing starts at the click: the browser SDK (when an exporter is
  // configured) or, always, a traceparent the API continues.
  useEffect(() => {
    void startBrowserTracing();
  }, []);
  const [queryClient] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            staleTime: 30_000,
            refetchOnWindowFocus: false,
            // Offline, fetch() is answered by the service worker's cache, so
            // queries must not pause on navigator.onLine.
            networkMode: "always",
            retry: (failureCount, error) => {
              // Auth and permission failures are not transient.
              const status = (error as { status?: number }).status;
              if (status === 401 || status === 403 || status === 404) return false;
              return failureCount < 2;
            },
          },
        },
      }),
  );

  return (
    <ThemeProvider attribute="class" defaultTheme="system" enableSystem disableTransitionOnChange>
      <QueryClientProvider client={queryClient}>
        <TooltipProvider delay={200}>{children}</TooltipProvider>
        <Toaster
          position="bottom-right"
          closeButton
          toastOptions={{ classNames: { toast: "font-sans" } }}
        />
      </QueryClientProvider>
    </ThemeProvider>
  );
}
