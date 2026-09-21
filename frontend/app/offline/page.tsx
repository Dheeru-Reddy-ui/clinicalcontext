import { WifiOff } from "lucide-react";
import Link from "next/link";

import { Button } from "@/components/ui/button";

export const metadata = { title: "Offline" };

/**
 * Served by the service worker when a navigation fails with no network.
 * History, sessions and binders you have already opened still work from
 * cache; asking a new question does not, and this page says so plainly.
 */
export default function OfflinePage() {
  return (
    <main className="flex min-h-screen flex-col items-center justify-center gap-3 p-6 text-center">
      <WifiOff className="size-8 text-muted-foreground" aria-hidden />
      <h1 className="text-lg font-semibold tracking-tight">You&apos;re offline</h1>
      <p className="max-w-sm text-sm text-muted-foreground">
        Answers you have already read — your history, sessions and binders — are still available. Asking a new
        question needs a connection.
      </p>
      <div className="flex gap-2">
        <Button size="sm" variant="outline" nativeButton={false} render={<Link href="/app/history" />}>
          Open history
        </Button>
        <Button size="sm" variant="outline" nativeButton={false} render={<Link href="/app/binders" />}>
          Open binders
        </Button>
      </div>
    </main>
  );
}
