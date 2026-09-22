import path from "node:path";

import { withSentryConfig } from "@sentry/nextjs";
import withSerwistInit from "@serwist/next";
import type { NextConfig } from "next";

const withSerwist = withSerwistInit({
  swSrc: "app/sw.ts",
  swDest: "public/sw.js",
  // The worker is built into the production bundle only; in dev the app
  // behaves as a normal SPA so HMR is not fighting a cache.
  disable: process.env.NODE_ENV === "development",
  reloadOnOnline: true,
});

import { connectSources, readCspEnvironment } from "./lib/csp";

// The Content-Security-Policy itself is set per request by the middleware
// (lib/csp.ts explains why: Next's inline bootstrap scripts need a nonce).
// This runs at build time and keeps the two build-failing checks — a missing
// or localhost API origin — where they belong: before anything ships.
connectSources(readCspEnvironment());

const nextConfig: NextConfig = {
  // A production build and `next dev` share .next, and the build deletes the
  // manifests the running dev server is holding open — which leaves the dev
  // server serving 500s until it is restarted. scripts/preflight.py sets this
  // so a verification build never disturbs a dev server.
  ...(process.env.NEXT_DIST_DIR ? { distDir: process.env.NEXT_DIST_DIR } : {}),
  async headers() {
    const security = [
      { key: "X-Content-Type-Options", value: "nosniff" },
      { key: "X-Frame-Options", value: "DENY" },
      { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
      { key: "Permissions-Policy", value: "camera=(), geolocation=(), interest-cohort=()" },
      // The voice page needs the microphone; nothing else does, and no
      // third-party frame ever does.
      { key: "Cross-Origin-Opener-Policy", value: "same-origin" },
    ];
    // HSTS only where there is TLS: on localhost it would break plain HTTP
    // for every project on the host for a year.
    if (process.env.NODE_ENV === "production") {
      security.push({ key: "Strict-Transport-Security", value: "max-age=31536000; includeSubDomains" });
    }
    return [{ source: "/:path*", headers: security }];
  },
  // Pin the workspace root: a stray lockfile above the repo otherwise makes
  // Next.js guess the wrong root (and breaks file tracing in CI).
  turbopack: {
    root: path.join(__dirname),
  },
  // The API is a separate origin; nothing here needs image optimisation.
  images: { unoptimized: true },
};

// Sentry wraps the build only to upload source maps when an auth token is
// present; without one it is inert and the DSN-less client init is a no-op.
export default withSentryConfig(withSerwist(nextConfig), {
  silent: true,
  disableLogger: true,
  sourcemaps: { disable: !process.env.SENTRY_AUTH_TOKEN },
  telemetry: false,
});
