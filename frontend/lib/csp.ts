/**
 * The Content-Security-Policy, in one place (Phase 14.4, corrected on the
 * first real deployment).
 *
 * Next.js's App Router bootstraps every page with inline scripts — the RSC
 * payload arrives as `self.__next_f.push(...)` — so a policy of
 * `script-src 'self'` blocks the framework itself and the page never
 * hydrates: a blank screen with nine CSP violations in the console. That is
 * exactly what the first deployment showed, because the policy had only
 * ever been loaded against the dev server, which allows inline scripts.
 *
 * The standard answer is a per-request nonce with 'strict-dynamic': the
 * middleware mints one, puts it on the request so Next stamps it onto every
 * script tag it emits, and puts the finished policy on the response. Scripts
 * without the nonce — anything injected — still do not run. Styles keep
 * 'unsafe-inline' for Tailwind's runtime injection; that is a far smaller
 * surface than scripts.
 *
 * Edge-safe: the middleware runs on the Edge runtime, so nothing here may
 * touch Node APIs.
 */

import { supabaseUrl } from "./supabase/env";

export interface CspEnvironment {
  apiUrl: string | undefined;
  supabaseUrl: string | undefined;
  otelExporterUrl: string | undefined;
  sentryDsn: string | undefined;
  production: boolean;
}

export function readCspEnvironment(): CspEnvironment {
  return {
    apiUrl: process.env.NEXT_PUBLIC_API_URL,
    supabaseUrl: supabaseUrl() || undefined,
    otelExporterUrl: process.env.NEXT_PUBLIC_OTEL_EXPORTER_URL,
    sentryDsn: process.env.NEXT_PUBLIC_SENTRY_DSN,
    production: process.env.NODE_ENV === "production",
  };
}

const LOCAL_ORIGIN = /localhost|127\.0\.0\.1/;

/**
 * The origins the browser may connect to. Throws in a production build for
 * the two mistakes that produce a blank app with nothing in the server
 * logs: no API origin at all, or a localhost one left over from a .env.
 */
export function connectSources(env: CspEnvironment): string[] {
  if (env.production && !env.apiUrl) {
    throw new Error(
      "NEXT_PUBLIC_API_URL must be set for a production build: the Content-Security-Policy " +
        "is derived from it, and without it the app cannot reach its own API.",
    );
  }
  const api = env.apiUrl ?? "http://localhost:8010";
  const websocket = api.replace(/^http/, "ws");
  const connect = [
    "'self'",
    api,
    websocket,
    env.supabaseUrl ?? "",
    env.otelExporterUrl ?? "",
    env.sentryDsn ? "https://*.ingest.sentry.io" : "",
  ].filter(Boolean);
  if (env.production) {
    const local = connect.filter((origin) => LOCAL_ORIGIN.test(origin));
    if (local.length > 0) {
      throw new Error(
        `Production build has localhost origins in its Content-Security-Policy: ${local.join(", ")}. ` +
          "Set NEXT_PUBLIC_SUPABASE_URL / NEXT_PUBLIC_OTEL_EXPORTER_URL to their deployed values, " +
          "or leave them unset.",
      );
    }
  }
  return connect;
}

/** The policy for one response, given that response's nonce. */
export function contentSecurityPolicy(env: CspEnvironment, nonce: string): string {
  // React Refresh needs eval in development; nothing else ever does.
  const scriptExtras = env.production ? "" : " 'unsafe-eval'";
  return [
    "default-src 'self'",
    `script-src 'self' 'nonce-${nonce}' 'strict-dynamic'${scriptExtras}`,
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data: blob:",
    "font-src 'self' data:",
    `connect-src ${connectSources(env).join(" ")}`,
    "media-src 'self' blob:",
    "worker-src 'self' blob:",
    "frame-ancestors 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "object-src 'none'",
  ].join("; ");
}

/** A fresh nonce: 128 bits of randomness, base64 — what CSP expects. */
export function mintNonce(): string {
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  let binary = "";
  for (const b of bytes) binary += String.fromCharCode(b);
  return btoa(binary);
}
