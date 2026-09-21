"use client";

import { AlertOctagon, RotateCcw } from "lucide-react";
import Link from "next/link";
import { useEffect } from "react";

import { Button } from "@/components/ui/button";

/**
 * Route-segment error boundary for everything under /app (spec #13).
 * A component crash lands here with a way back, not on a blank page. The
 * digest is shown so a report can be matched to server logs.
 */
export default function AppError({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  useEffect(() => {
    // Surface in the console for local debugging; production reporting is Phase 12.
    console.error(error);
  }, [error]);

  return (
    <div className="mx-auto flex max-w-lg flex-col items-center gap-3 p-10 text-center" role="alert">
      <AlertOctagon className="size-8 text-destructive" aria-hidden />
      <h1 className="text-lg font-semibold tracking-tight">Something went wrong on this screen</h1>
      <p className="text-sm text-muted-foreground">
        The rest of the app is unaffected. You can retry this screen, or go back to Ask.
      </p>
      {error.digest && <p className="font-mono text-[11px] text-muted-foreground">ref {error.digest}</p>}
      <div className="mt-2 flex gap-2">
        <Button size="sm" onClick={reset}>
          <RotateCcw /> Try again
        </Button>
        <Button size="sm" variant="outline" nativeButton={false} render={<Link href="/app" />}>
          Back to Ask
        </Button>
      </div>
    </div>
  );
}
