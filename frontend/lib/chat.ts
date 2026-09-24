/**
 * The chat assistant over the wire: POST /api/v1/chat (signed in) and
 * POST /api/public/chat (the website's chatbot) as server-sent events.
 *
 * Event shapes mirror backend/app/assistant/chat.py. Each is narrowed here,
 * so nothing in the UI reads an untyped field.
 */

import { apiUrl } from "@/lib/api";
import { asCitations, type Citation } from "@/lib/domain";
import { noteResponse, traceHeaders } from "@/lib/telemetry";

export type Audience = "patient" | "clinician" | "student";
export type ChatKind = "chat" | "learn" | "treatment";

export interface ChatRequestBody {
  message: string;
  audience: Audience;
  session_id?: string | null;
  kind?: ChatKind;
  specialty?: string | null;
  level?: "mbbs" | "pg" | null;
}

export interface PublicTurn {
  question: string;
  answer: string;
}

export interface SourceCheck {
  backed: number;
  partly: number;
  unmatched: number;
  general: number;
}

export interface LiveSearch {
  term: string;
  searched: boolean;
  found: number;
  added: number;
  source: string;
}

export interface ChatResult {
  answerId: string | null;
  queryId: string | null;
  sessionId: string | null;
  answer: string;
  citations: Citation[];
  provider: string;
  model: string;
  mode: "llm" | "extractive" | "conversation";
  check: SourceCheck | null;
  live: LiveSearch | null;
  labelsAdded: number;
  escalation: string | null;
  suggestCheck: string | null;
  latencyMs: number;
}

export type ChatEvent =
  | { stage: "accepted"; sessionId: string | null; queryId: string | null }
  | { stage: "token"; text: string }
  | { stage: "escalation"; message: string }
  | { stage: "blocked"; message: string; code: string }
  | { stage: "result"; result: ChatResult }
  | { stage: "error"; message: string }
  | { stage: "progress"; key: string; message: string };

export class ChatError extends Error {
  constructor(
    public readonly status: number,
    message: string,
    public readonly retryAfter: number | null = null,
  ) {
    super(message);
    this.name = "ChatError";
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function str(value: unknown): string | null {
  return typeof value === "string" ? value : null;
}

function asCheck(value: unknown): SourceCheck | null {
  if (!isRecord(value) || typeof value.backed !== "number") return null;
  const n = (k: string) => (typeof value[k] === "number" ? (value[k] as number) : 0);
  return { backed: n("backed"), partly: n("partly"), unmatched: n("unmatched"), general: n("general") };
}

function asLive(value: unknown): LiveSearch | null {
  if (!isRecord(value)) return null;
  return {
    term: str(value.term) ?? "",
    searched: value.searched === true,
    found: typeof value.found === "number" ? value.found : 0,
    added: typeof value.added === "number" ? value.added : 0,
    source: str(value.source) ?? "pubmed",
  };
}

export function asChatResult(data: Record<string, unknown>): ChatResult {
  const mode = data.mode === "llm" || data.mode === "conversation" ? data.mode : "extractive";
  return {
    answerId: str(data.answer_id),
    queryId: str(data.query_id),
    sessionId: str(data.session_id),
    answer: str(data.answer) ?? "",
    citations: asCitations(data.citations),
    provider: str(data.provider) ?? "local",
    model: str(data.model) ?? "",
    mode,
    check: asCheck(data.check),
    live: asLive(data.live),
    labelsAdded: typeof data.labels_added === "number" ? data.labels_added : 0,
    escalation: str(data.escalation),
    suggestCheck: str(data.suggest_check),
    latencyMs: typeof data.latency_ms === "number" ? data.latency_ms : 0,
  };
}

export function parseChatEvent(raw: string): ChatEvent | null {
  let value: unknown;
  try {
    value = JSON.parse(raw);
  } catch {
    return null;
  }
  if (!isRecord(value) || typeof value.stage !== "string") return null;
  const message = str(value.message) ?? "";
  const data = isRecord(value.data) ? value.data : {};
  switch (value.stage) {
    case "accepted":
      return { stage: "accepted", sessionId: str(data.session_id), queryId: str(data.query_id) };
    case "token":
      return { stage: "token", text: str(data.text) ?? "" };
    case "escalation":
      return { stage: "escalation", message };
    case "blocked":
      return { stage: "blocked", message, code: str(data.code) ?? "blocked" };
    case "result":
      return { stage: "result", result: asChatResult(data) };
    case "error":
      return { stage: "error", message: message || "Something went wrong." };
    default:
      return { stage: "progress", key: value.stage, message };
  }
}

async function* readEvents(response: Response): AsyncGenerator<ChatEvent, void, void> {
  if (!response.ok || !response.body) {
    const envelope = (await response.json().catch(() => ({}))) as {
      error?: { message?: string };
    };
    const retryAfter = response.headers.get("retry-after");
    throw new ChatError(
      response.status,
      envelope.error?.message ??
        (response.status === 429
          ? "Too many messages — wait a moment and try again."
          : `The request failed (${response.status}).`),
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
      let boundary = buffer.indexOf("\n\n");
      while (boundary !== -1) {
        const frame = buffer.slice(0, boundary);
        buffer = buffer.slice(boundary + 2);
        for (const line of frame.split("\n")) {
          if (!line.startsWith("data: ")) continue;
          const event = parseChatEvent(line.slice(6));
          if (event) yield event;
        }
        boundary = buffer.indexOf("\n\n");
      }
    }
    const tail = buffer.trim();
    if (tail.startsWith("data: ")) {
      const event = parseChatEvent(tail.slice(6));
      if (event) yield event;
    }
  } finally {
    reader.releaseLock();
  }
}

export async function* streamChat(
  body: ChatRequestBody,
  accessToken: string,
  signal?: AbortSignal,
): AsyncGenerator<ChatEvent, void, void> {
  const response = await fetch(apiUrl("/api/v1/chat"), {
    method: "POST",
    headers: {
      ...traceHeaders(),
      "Content-Type": "application/json",
      Accept: "text/event-stream",
      Authorization: `Bearer ${accessToken}`,
    },
    body: JSON.stringify(body),
    signal,
    cache: "no-store",
  });
  noteResponse(response);
  yield* readEvents(response);
}

export async function* streamPublicChat(
  message: string,
  audience: Audience,
  history: PublicTurn[],
  signal?: AbortSignal,
): AsyncGenerator<ChatEvent, void, void> {
  const response = await fetch(apiUrl("/api/public/chat"), {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
    body: JSON.stringify({ message, audience, history: history.slice(-4) }),
    signal,
    cache: "no-store",
  });
  yield* readEvents(response);
}

/** Plain-language labels for the progress stages the assistant reports. */
export const PROGRESS_LABELS: Record<string, string> = {
  searching: "Searching the medical library",
  live_search: "Searching PubMed for current research",
  drug_label: "Checking official drug labels",
  live_found: "Found sources",
  writing: "Writing the answer",
  fallback: "Answering from the sources",
  checking: "Checking each statement against its source",
};

export const AUDIENCE_LABELS: Record<Audience, { label: string; hint: string }> = {
  patient: { label: "Patient", hint: "Plain language, safety first" },
  clinician: { label: "Doctor", hint: "Guideline detail, doses, evidence" },
  student: { label: "Student", hint: "Structured teaching, exam pearls" },
};
