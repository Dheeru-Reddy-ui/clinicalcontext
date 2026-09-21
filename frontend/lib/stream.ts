/**
 * Consume POST /api/v1/queries as a stream of typed events.
 *
 * The backend emits `text/event-stream` framed as `data: {json}\n\n`. Events
 * are surfaced one at a time as they arrive so the UI can show reasoning
 * steps live and grow the answer token by token. Abortable via the signal so
 * navigating away cancels the request, not just the rendering.
 */

import { apiUrl } from "@/lib/api";
import { parseStreamEvent, type Schemas, type StreamEvent } from "@/lib/domain";

export type QueryRequest = Schemas["QueryRequest"];

import { noteResponse, traceHeaders, withSpan } from "@/lib/telemetry";

export class StreamError extends Error {
  constructor(
    public readonly status: number,
    public readonly code: string,
    message: string,
    public readonly retryAfter: number | null = null,
  ) {
    super(message);
    this.name = "StreamError";
  }
}

export async function* streamQuery(
  body: QueryRequest,
  accessToken: string,
  options: { signal?: AbortSignal; idempotencyKey?: string } = {},
): AsyncGenerator<StreamEvent, void, void> {
  // The click is the root of the trace: the streaming request, the graph and
  // every provider call hang off this span when the browser SDK is on, and
  // the traceparent is read inside it so the request continues *this* span.
  const response = await withSpan("ask.click", { "query.mode": body.mode ?? "standard" }, () => {
    const headers: Record<string, string> = {
      ...traceHeaders(),
      "Content-Type": "application/json",
      Accept: "text/event-stream",
      Authorization: `Bearer ${accessToken}`,
    };
    if (options.idempotencyKey) headers["Idempotency-Key"] = options.idempotencyKey;
    return fetch(apiUrl("/api/v1/queries"), {
      method: "POST",
      headers,
      body: JSON.stringify(body),
      signal: options.signal,
      cache: "no-store",
    });
  });
  noteResponse(response);

  if (!response.ok || !response.body) {
    const envelope = (await response.json().catch(() => ({}))) as {
      error?: { code?: string; message?: string };
    };
    const retryAfter = response.headers.get("retry-after");
    throw new StreamError(
      response.status,
      envelope.error?.code ?? "stream_failed",
      envelope.error?.message ?? `The request failed (${response.status}).`,
      retryAfter ? Number(retryAfter) : null,
    );
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      // Frames end with a blank line; a partial frame stays in the buffer.
      let boundary = buffer.indexOf("\n\n");
      while (boundary !== -1) {
        const frame = buffer.slice(0, boundary);
        buffer = buffer.slice(boundary + 2);
        for (const line of frame.split("\n")) {
          if (line.startsWith("data: ")) yield parseStreamEvent(line.slice(6));
        }
        boundary = buffer.indexOf("\n\n");
      }
    }
    if (buffer.trim().startsWith("data: ")) {
      yield parseStreamEvent(buffer.trim().slice(6));
    }
  } finally {
    reader.releaseLock();
  }
}
