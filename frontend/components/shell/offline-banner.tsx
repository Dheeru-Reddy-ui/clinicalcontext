"use client";

import { WifiOff } from "lucide-react";
import { useSyncExternalStore } from "react";

function subscribe(onChange: () => void) {
  window.addEventListener("online", onChange);
  window.addEventListener("offline", onChange);
  return () => {
    window.removeEventListener("online", onChange);
    window.removeEventListener("offline", onChange);
  };
}

export function useOnline(): boolean {
  return useSyncExternalStore(subscribe, () => navigator.onLine, () => true);
}

/**
 * Spec #23: cached content must be clearly badged. While offline, everything
 * on screen came from the service worker's cache, and new queries are
 * impossible — say both, once, at the top.
 */
export function OfflineBanner() {
  const online = useOnline();
  if (online) return null;
  return (
    <div role="status" className="flex items-center gap-2 border-b bg-cached-bg px-4 py-1.5 text-xs text-cached-fg">
      <WifiOff className="size-3.5" aria-hidden />
      <span className="font-medium">Offline.</span>
      <span>Showing cached history and binders. New questions need a connection.</span>
    </div>
  );
}
