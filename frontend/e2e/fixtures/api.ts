import { env } from "./env";

/** Thin API client for the assertions and set-up the specs need. */

export interface SseEvent {
  stage: string;
  message?: string;
  data: Record<string, unknown>;
}

interface CallOptions {
  token?: string;
  apiKey?: string;
  method?: string;
  body?: unknown;
  headers?: Record<string, string>;
}

export async function call(path: string, options: CallOptions = {}): Promise<Response> {
  const headers: Record<string, string> = { ...options.headers };
  if (options.body !== undefined) headers["Content-Type"] = "application/json";
  if (options.token) headers.Authorization = `Bearer ${options.token}`;
  if (options.apiKey) headers["X-API-Key"] = options.apiKey;
  return fetch(`${env.apiUrl}${path}`, {
    method: options.method ?? (options.body === undefined ? "GET" : "POST"),
    headers,
    body: options.body === undefined ? undefined : JSON.stringify(options.body),
  });
}

export async function json<T>(path: string, options: CallOptions = {}): Promise<T> {
  const response = await call(path, options);
  if (!response.ok) {
    throw new Error(`${options.method ?? "GET"} ${path} → ${response.status}: ${await response.text()}`);
  }
  return (await response.json()) as T;
}

/** POST /api/v1/queries and collect the whole SSE stream. */
export async function ask(
  question: string,
  options: CallOptions & { idempotencyKey?: string; mode?: string; entities?: string[] } = {},
): Promise<{ events: SseEvent[]; status: number; headers: Headers }> {
  const headers = { ...(options.headers ?? {}) };
  if (options.idempotencyKey) headers["Idempotency-Key"] = options.idempotencyKey;
  const response = await call("/api/v1/queries", {
    ...options,
    method: "POST",
    headers,
    body: {
      query: question,
      ...(options.mode ? { mode: options.mode } : {}),
      ...(options.entities ? { entities: options.entities } : {}),
    },
  });
  const text = response.ok ? await response.text() : "";
  return { events: parseSse(text), status: response.status, headers: response.headers };
}

export function parseSse(body: string): SseEvent[] {
  const events: SseEvent[] = [];
  for (const line of body.split(/\r?\n/)) {
    if (line.startsWith("data: ")) events.push(JSON.parse(line.slice(6)) as SseEvent);
  }
  return events;
}

export function stageOf(events: SseEvent[], stage: string): SseEvent | undefined {
  return events.find((e) => e.stage === stage);
}

export function resultOf(events: SseEvent[]): Record<string, unknown> {
  const result = stageOf(events, "result");
  if (!result) throw new Error(`no result event; stages: ${events.map((e) => e.stage).join(", ")}`);
  return result.data;
}
