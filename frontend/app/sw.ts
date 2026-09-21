import { defaultCache } from "@serwist/next/worker";
import type { PrecacheEntry, SerwistGlobalConfig } from "serwist";
import { NetworkFirst, NetworkOnly, Serwist } from "serwist";

/**
 * Service worker (spec #23).
 *
 * Cache strategy, deliberately conservative for a clinical tool:
 *  - App shell + static assets: precached at install (Serwist's manifest).
 *  - Read endpoints the user may want offline — their history, sessions,
 *    query details and binders — are NetworkFirst with a short timeout, so a
 *    live answer always wins and the cache only serves when the network is
 *    gone. The UI badges these as cached when offline.
 *  - Everything that *runs* a query (POST /queries, the SSE stream, uploads,
 *    feedback) is NetworkOnly. Querying obviously requires connectivity; the
 *    worker must never fake a fresh answer from cache.
 *  - Auth and the public permalink are never cached here.
 */

declare global {
  interface WorkerGlobalScope extends SerwistGlobalConfig {
    __SW_MANIFEST: (PrecacheEntry | string)[] | undefined;
  }
}

declare const self: ServiceWorkerGlobalScope;

const API_ORIGIN = (() => {
  try {
    return new URL(process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000").origin;
  } catch {
    return "";
  }
})();

const OFFLINE_READ = /\/api\/v1\/(queries|sessions|binders|documents|notifications)(\/|\?|$)/;

const serwist = new Serwist({
  precacheEntries: self.__SW_MANIFEST,
  skipWaiting: true,
  clientsClaim: true,
  navigationPreload: true,
  runtimeCaching: [
    {
      // Never cache anything that runs or mutates.
      matcher: ({ request, url }) => url.origin === API_ORIGIN && request.method !== "GET",
      handler: new NetworkOnly(),
    },
    {
      matcher: ({ url }) => url.origin === API_ORIGIN && /\/(auth|public)\//.test(url.pathname),
      handler: new NetworkOnly(),
    },
    {
      // Offline read access to the user's own record: history, threads, binders.
      matcher: ({ request, url }) => url.origin === API_ORIGIN && request.method === "GET" && OFFLINE_READ.test(url.pathname),
      handler: new NetworkFirst({
        cacheName: "cc-api-reads",
        networkTimeoutSeconds: 4,
        plugins: [
          {
            // Keep only successful JSON; never cache an auth failure.
            cacheWillUpdate: async ({ response }) => (response.status === 200 ? response : null),
          },
        ],
      }),
    },
    ...defaultCache,
  ],
  fallbacks: {
    entries: [
      {
        url: "/offline",
        matcher: ({ request }) => request.destination === "document",
      },
    ],
  },
});

serwist.addEventListeners();
