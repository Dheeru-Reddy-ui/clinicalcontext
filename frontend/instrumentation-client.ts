/**
 * Sentry in the browser (Phase 13). Off without a DSN; every event carries the
 * release and the ids of the last backend request so an error can be matched
 * to its trace and its log lines.
 */
import * as Sentry from "@sentry/nextjs";

import { currentIds, release } from "@/lib/telemetry";

Sentry.init({
  dsn: process.env.NEXT_PUBLIC_SENTRY_DSN || undefined,
  release,
  environment: process.env.NEXT_PUBLIC_ENVIRONMENT ?? "local",
  tracesSampleRate: 0,
  sendDefaultPii: false,
  beforeSend(event) {
    const { traceId, requestId } = currentIds();
    event.tags = { ...event.tags, ...(requestId ? { request_id: requestId } : {}), ...(traceId ? { trace_id: traceId } : {}) };
    return event;
  },
});

export const onRouterTransitionStart = Sentry.captureRouterTransitionStart;
