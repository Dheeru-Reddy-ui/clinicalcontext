"use client";

import { useRouter } from "next/navigation";
import { useEffect, type ReactNode } from "react";

import { AuthProvider, useAuth } from "@/components/providers/auth-provider";
import { AppShell } from "@/components/shell/app-shell";
import { CommandPaletteProvider } from "@/components/shell/command-palette";
import { Skeleton } from "@/components/ui/skeleton";

/**
 * Shell for every authenticated screen. The middleware already guarantees a
 * Supabase session; this layout routes members without an org to onboarding
 * and mounts the navigation + command palette around the page.
 */
export default function AppLayout({ children }: { children: ReactNode }) {
  return (
    <AuthProvider>
      <AuthedShell>{children}</AuthedShell>
    </AuthProvider>
  );
}

function AuthedShell({ children }: { children: ReactNode }) {
  const { loading, me, needsOnboarding } = useAuth();
  const router = useRouter();

  useEffect(() => {
    if (!loading && needsOnboarding) router.replace("/onboarding");
  }, [loading, needsOnboarding, router]);

  if (loading || (!me && !needsOnboarding)) {
    return (
      <div className="flex min-h-screen" aria-busy aria-label="Loading workspace">
        <div className="hidden w-56 border-r bg-sidebar p-3 md:block">
          <Skeleton className="mb-6 h-6 w-32" />
          {Array.from({ length: 6 }).map((_, i) => (
            <Skeleton key={i} className="mb-2 h-7 w-full" />
          ))}
        </div>
        <div className="flex-1 p-8">
          <Skeleton className="mb-4 h-8 w-64" />
          <Skeleton className="h-32 w-full max-w-3xl" />
        </div>
      </div>
    );
  }

  if (needsOnboarding) return null; // redirecting

  return (
    <CommandPaletteProvider>
      <AppShell>{children}</AppShell>
    </CommandPaletteProvider>
  );
}
