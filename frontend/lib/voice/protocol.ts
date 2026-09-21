/**
 * The voice WebSocket protocol, mirroring `backend/app/voice/protocol.py`.
 *
 * Text frames are JSON events; binary frames are TTS audio with a 6-byte
 * header (kind u8 | turn u16 | sentence u16 | flags u8, little-endian) so a
 * frame from a flushed turn can be dropped on arrival.
 */

import type { AnswerResultData } from "@/lib/domain";

export const SAMPLE_RATE = 16_000;
export const FRAME_MS = 20;

export type VoiceState =
  | "IDLE"
  | "LISTENING"
  | "ENDPOINTING"
  | "PROCESSING"
  | "SPEAKING"
  | "BARGE_IN"
  | "CONFIRMING"
  | "CLOSED";

export type SentenceKind =
  | "answer"
  | "offer"
  | "refusal"
  | "escalation"
  | "abstention"
  | "confirmation"
  | "mask"
  | "system";

export interface WordOut {
  text: string;
  confidence: number;
}

export interface CorrectionOut {
  original: string;
  corrected: string;
  score: number;
}

export interface WaterfallLegs {
  endpoint_decision: number | null;
  transcript_final: number | null;
  guardrails: number | null;
  retrieval: number | null;
  llm_first_token: number | null;
  first_sentence: number | null;
  tts_ttfb: number | null;
  client_playback: number | null;
}

export type ServerEvent =
  | {
      type: "session";
      session_id: string;
      resume_token: string;
      query_session_id: string | null;
      backend: string;
      stt_model: string;
      tts_model: string;
      sample_rate: number;
      state: VoiceState;
      resumed: boolean;
    }
  | { type: "state"; state: VoiceState; from_state: VoiceState; reason: string; turn: number }
  | { type: "partial"; turn: number; text: string; words: WordOut[] }
  | {
      type: "final";
      turn: number;
      text: string;
      raw_text: string;
      corrections: CorrectionOut[];
      source: "voice" | "text";
    }
  | {
      type: "endpoint";
      turn: number;
      layer: "vad" | "semantic" | "ceiling" | "text";
      silence_ms: number;
      complete: boolean;
      decision_ms: number;
    }
  | {
      type: "speculation";
      turn: number;
      fired: boolean;
      hit: boolean | null;
      similarity: number | null;
      wasted: boolean;
    }
  | {
      type: "guardrail";
      turn: number;
      verdict: "blocked" | "escalation";
      blocked_by: string | null;
      code: string | null;
      message: string;
      spoken: string;
      query_session_id?: string | null;
    }
  | {
      type: "confirm_request";
      turn: number;
      heard: string;
      options: Array<{ name: string; description: string }>;
      prompt: string;
    }
  | {
      type: "agent_sentence";
      turn: number;
      index: number;
      text: string;
      spoken_text: string;
      markers: number[];
      kind: SentenceKind;
    }
  | { type: "audio_start"; turn: number; index: number; sample_rate: number }
  | { type: "audio_end"; turn: number; index: number; last: boolean }
  | { type: "result"; turn: number; query_id: string | null; query_session_id?: string | null; data: AnswerResultData }
  | { type: "offer"; turn: number; kind: "walkthrough" | "more"; remaining_sentences: number }
  | {
      type: "waterfall";
      turn: number;
      legs: WaterfallLegs;
      total_first_audio_ms: number | null;
      client_first_audio_ms: number | null;
      speculation: Record<string, unknown>;
      waste: Record<string, unknown>;
      mask_used: boolean;
    }
  | { type: "error"; code: string; message: string }
  | { type: "pong" };

const KNOWN_TYPES = new Set<ServerEvent["type"]>([
  "session",
  "state",
  "partial",
  "final",
  "endpoint",
  "speculation",
  "guardrail",
  "confirm_request",
  "agent_sentence",
  "audio_start",
  "audio_end",
  "result",
  "offer",
  "waterfall",
  "error",
  "pong",
]);

export function parseServerEvent(raw: string): ServerEvent | null {
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return null;
  }
  if (typeof parsed !== "object" || parsed === null) return null;
  const type = (parsed as { type?: unknown }).type;
  if (typeof type !== "string" || !KNOWN_TYPES.has(type as ServerEvent["type"])) return null;
  return parsed as ServerEvent;
}

export type ClientMessage =
  | { type: "start"; token: string; query_session_id: string | null; client: ClientCapabilities }
  | { type: "resume"; token: string; session_id: string; resume_token: string }
  | { type: "text"; text: string }
  | { type: "confirm"; choice: string }
  | { type: "continue" }
  | { type: "barge_in"; stop_latency_ms: number }
  | {
      type: "playback";
      turn: number;
      sentence: number;
      event: "started" | "ended";
      buffer_ms?: number;
    }
  | { type: "stop" }
  | { type: "ping" };

export interface ClientCapabilities {
  sample_rate: number;
  echo_cancellation: boolean;
  user_agent: string | null;
}

export interface AudioFrame {
  turn: number;
  sentence: number;
  flags: number;
  pcm: ArrayBuffer;
}

const HEADER_BYTES = 6;

export function unpackAudio(buffer: ArrayBuffer): AudioFrame | null {
  if (buffer.byteLength < HEADER_BYTES) return null;
  const view = new DataView(buffer);
  if (view.getUint8(0) !== 1) return null;
  return {
    turn: view.getUint16(1, true),
    sentence: view.getUint16(3, true),
    flags: view.getUint8(5),
    pcm: buffer.slice(HEADER_BYTES),
  };
}

export const LEG_LABELS: Record<keyof WaterfallLegs, string> = {
  endpoint_decision: "Endpoint decision",
  transcript_final: "Final transcript",
  guardrails: "Guardrails",
  retrieval: "Retrieval",
  llm_first_token: "LLM first token",
  first_sentence: "First sentence",
  tts_ttfb: "TTS first byte",
  client_playback: "Client playback",
};

export const LEG_ORDER: Array<keyof WaterfallLegs> = [
  "endpoint_decision",
  "transcript_final",
  "guardrails",
  "retrieval",
  "llm_first_token",
  "first_sentence",
  "tts_ttfb",
  "client_playback",
];
