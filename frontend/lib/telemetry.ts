/**
 * Browser-side tracing (Phase 13): the trace starts at the click.
 *
 * Two layers, so a trace is always correlatable and, when an exporter is
 * configured, the browser's own spans sit at the top of it:
 *
 * 1. Every backend fetch carries a W3C `traceparent`. With no exporter the
 *    browser mints the trace id itself, the API continues it, and the id is
 *    kept on the response (`lastTraceId`) so an error report or a support
 *    conversation can name the exact trace.
 * 2. With `NEXT_PUBLIC_OTEL_EXPORTER_URL` set (an OTLP/HTTP collector such as
 *    Jaeger's 4318 port), the OpenTelemetry web SDK exports real browser
 *    spans — an `ask.click` span around the streaming query and a span per
 *    fetch — with the same trace ids the backend sees.
 *
 * Both are no-ops on the server (Next.js renders some pages there).
 */

import { context, propagation, trace, type Span, type Tracer } from "@opentelemetry/api";

let started = false;
let webTracer: Tracer | null = null;
let lastTraceId: string | null = null;
let lastRequestId: string | null = null;

const exporterUrl = process.env.NEXT_PUBLIC_OTEL_EXPORTER_URL ?? "";
export const release = process.env.NEXT_PUBLIC_RELEASE ?? "dev";

function randomHex(bytes: number): string {
  const buffer = new Uint8Array(bytes);
  crypto.getRandomValues(buffer);
  return Array.from(buffer, (b) => b.toString(16).padStart(2, "0")).join("");
}

/** Install the web SDK once, in the browser, when an exporter is configured. */
export async function startBrowserTracing(): Promise<void> {
  if (started || typeof window === "undefined") return;
  started = true;
  if (!exporterUrl) return;
  const [{ WebTracerProvider, BatchSpanProcessor }, { OTLPTraceExporter }, { ZoneContextManager }, { resourceFromAttributes }, { FetchInstrumentation }, { registerInstrumentations }] =
    await Promise.all([
      import("@opentelemetry/sdk-trace-web"),
      import("@opentelemetry/exporter-trace-otlp-http"),
      import("@opentelemetry/context-zone"),
      import("@opentelemetry/resources"),
      import("@opentelemetry/instrumentation-fetch"),
      import("@opentelemetry/instrumentation"),
    ]);
  const apiOrigin = new URL(process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000").origin;
  const provider = new WebTracerProvider({
    resource: resourceFromAttributes({
      "service.name": "clinicalcontext-web",
      "service.version": release,
    }),
    spanProcessors: [new BatchSpanProcessor(new OTLPTraceExporter({ url: `${exporterUrl.replace(/\/$/, "")}/v1/traces` }))],
  });
  provider.register({ contextManager: new ZoneContextManager() });
  registerInstrumentations({
    instrumentations: [
      new FetchInstrumentation({
        // The API is a different origin: the header must be allowed through CORS.
        propagateTraceHeaderCorsUrls: [new RegExp(`^${apiOrigin.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}`)],
        clearTimingResources: true,
      }),
    ],
  });
  webTracer = trace.getTracer("clinicalcontext-web", release);
}

/**
 * Headers for a backend request: a `traceparent` from the active span when
 * the SDK is on, otherwise a fresh trace id the API will continue.
 */
export function traceHeaders(): Record<string, string> {
  const carrier: Record<string, string> = {};
  propagation.inject(context.active(), carrier);
  if (!carrier.traceparent) {
    const traceId = randomHex(16);
    carrier.traceparent = `00-${traceId}-${randomHex(8)}-01`;
  }
  lastTraceId = carrier.traceparent.split("-")[1] ?? null;
  return carrier;
}

/** Remember what the API answered with, for error reports and support. */
export function noteResponse(response: Response): void {
  lastRequestId = response.headers.get("x-request-id") ?? lastRequestId;
}

export function currentIds(): { traceId: string | null; requestId: string | null } {
  return { traceId: lastTraceId, requestId: lastRequestId };
}

/**
 * Run `fn` inside a named browser span (a no-op wrapper when the SDK is
 * off, so callers never branch on configuration).
 */
export async function withSpan<T>(name: string, attributes: Record<string, string | number | boolean>, fn: (span: Span | null) => Promise<T>): Promise<T> {
  if (!webTracer) return fn(null);
  return webTracer.startActiveSpan(name, { attributes }, async (span) => {
    try {
      return await fn(span);
    } catch (error) {
      span.recordException(error as Error);
      span.setStatus({ code: 2, message: (error as Error).name });
      throw error;
    } finally {
      span.end();
    }
  });
}
