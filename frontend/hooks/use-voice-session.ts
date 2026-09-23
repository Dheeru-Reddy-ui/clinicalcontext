"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useReducer, useRef } from "react";

import { fromStreamResult, type AnswerModel } from "@/components/answer/answer-model";
import { useToken } from "@/hooks/use-api";
import { apiUrl } from "@/lib/api";
import { EnergyVad, VoiceAudio } from "@/lib/voice/audio-client";
import {
  parseServerEvent,
  unpackAudio,
  type ClientMessage,
  type CorrectionOut,
  type SentenceKind,
  type ServerEvent,
  type VoiceState,
  type WaterfallLegs,
  type WordOut,
} from "@/lib/voice/protocol";

/**
 * The voice session: one WebSocket + one AudioContext, driven by the server's
 * state machine. Everything the screen shows (11G) is derived from server
 * events; the two things decided *here* are the client-side barge-in (the
 * only place a ≤150 ms stop is possible) and playback-synchronized chips.
 */

export interface AgentSentence {
  index: number;
  text: string;
  spokenText: string;
  markers: number[];
  kind: SentenceKind;
  spoken: boolean;
}

export interface VoiceTurn {
  index: number;
  partial: string;
  partialWords: WordOut[];
  final: string | null;
  rawText: string | null;
  corrections: CorrectionOut[];
  source: "voice" | "text" | null;
  sentences: AgentSentence[];
  answer: AnswerModel | null;
  guardrail: { verdict: "blocked" | "escalation"; blockedBy: string | null; code: string | null; spoken: string } | null;
  confirm: { heard: string; options: Array<{ name: string; description: string }>; prompt: string } | null;
  offer: { kind: "walkthrough" | "more"; remaining: number } | null;
  waterfall: {
    legs: WaterfallLegs;
    totalFirstAudioMs: number | null;
    clientFirstAudioMs: number | null;
    speculation: Record<string, unknown>;
    waste: Record<string, unknown>;
    maskUsed: boolean;
  } | null;
  endpoint: { layer: string; silenceMs: number; decisionMs: number; complete: boolean } | null;
  bargeIns: number[];
}

export type Status = "idle" | "connecting" | "ready" | "reconnecting" | "error";

export interface VoiceSessionState {
  status: Status;
  error: string | null;
  sessionId: string | null;
  querySessionId: string | null;
  backend: string | null;
  sttModel: string | null;
  ttsModel: string | null;
  state: VoiceState;
  turnIndex: number;
  turns: VoiceTurn[];
  micLevel: number;
  agentLevel: number;
  echoCancellation: boolean;
  micAvailable: boolean;
  micError: string | null;
  resumed: boolean;
}

type Action =
  | { type: "status"; status: Status; error?: string | null }
  | { type: "event"; event: ServerEvent }
  | { type: "levels"; mic?: number; agent?: number }
  | { type: "spoken"; turn: number; sentence: number }
  | { type: "barge"; turn: number; stopMs: number }
  | { type: "echo"; enabled: boolean }
  | { type: "mic"; available: boolean; error: string | null }
  | { type: "reset" };

const emptyTurn = (index: number): VoiceTurn => ({
  index,
  partial: "",
  partialWords: [],
  final: null,
  rawText: null,
  corrections: [],
  source: null,
  sentences: [],
  answer: null,
  guardrail: null,
  confirm: null,
  offer: null,
  waterfall: null,
  endpoint: null,
  bargeIns: [],
});

const initial: VoiceSessionState = {
  status: "idle",
  error: null,
  sessionId: null,
  querySessionId: null,
  backend: null,
  sttModel: null,
  ttsModel: null,
  state: "IDLE",
  turnIndex: 0,
  turns: [],
  micLevel: 0,
  agentLevel: 0,
  echoCancellation: false,
  micAvailable: false,
  micError: null,
  resumed: false,
};

function withTurn(state: VoiceSessionState, index: number, patch: (turn: VoiceTurn) => VoiceTurn): VoiceSessionState {
  const turns = [...state.turns];
  while (turns.length <= index) turns.push(emptyTurn(turns.length));
  const current = turns[index] ?? emptyTurn(index);
  turns[index] = patch(current);
  return { ...state, turns, turnIndex: Math.max(state.turnIndex, index) };
}

function reduce(state: VoiceSessionState, action: Action): VoiceSessionState {
  switch (action.type) {
    case "reset":
      return initial;
    case "status":
      return { ...state, status: action.status, error: action.error ?? (action.status === "error" ? state.error : null) };
    case "levels":
      return { ...state, micLevel: action.mic ?? state.micLevel, agentLevel: action.agent ?? state.agentLevel };
    case "echo":
      return { ...state, echoCancellation: action.enabled };
    case "mic":
      return { ...state, micAvailable: action.available, micError: action.error };
    case "spoken":
      return withTurn(state, action.turn, (turn) => ({
        ...turn,
        sentences: turn.sentences.map((s) => (s.index === action.sentence ? { ...s, spoken: true } : s)),
      }));
    case "barge":
      return withTurn(state, action.turn, (turn) => ({ ...turn, bargeIns: [...turn.bargeIns, action.stopMs] }));
    case "event": {
      const e = action.event;
      switch (e.type) {
        case "session":
          return {
            ...state,
            status: "ready",
            sessionId: e.session_id,
            querySessionId: e.query_session_id,
            backend: e.backend,
            sttModel: e.stt_model,
            ttsModel: e.tts_model,
            state: e.state,
            resumed: e.resumed,
          };
        case "state":
          return { ...state, state: e.state, turnIndex: Math.max(state.turnIndex, e.turn) };
        case "partial":
          return withTurn(state, e.turn, (turn) => ({ ...turn, partial: e.text, partialWords: e.words }));
        case "final":
          return withTurn(state, e.turn, (turn) => ({
            ...turn,
            final: e.text,
            rawText: e.raw_text,
            corrections: e.corrections,
            source: e.source,
            partial: "",
            partialWords: [],
            confirm: null,
          }));
        case "endpoint":
          return withTurn(state, e.turn, (turn) => ({
            ...turn,
            endpoint: { layer: e.layer, silenceMs: e.silence_ms, decisionMs: e.decision_ms, complete: e.complete },
          }));
        case "guardrail":
          return withTurn({ ...state, querySessionId: e.query_session_id ?? state.querySessionId }, e.turn, (turn) => ({
            ...turn,
            guardrail: { verdict: e.verdict, blockedBy: e.blocked_by, code: e.code, spoken: e.spoken },
          }));
        case "confirm_request":
          return withTurn(state, e.turn, (turn) => ({
            ...turn,
            confirm: { heard: e.heard, options: e.options, prompt: e.prompt },
          }));
        case "agent_sentence":
          return withTurn(state, e.turn, (turn) => ({
            ...turn,
            sentences: [
              ...turn.sentences.filter((s) => s.index !== e.index),
              { index: e.index, text: e.text, spokenText: e.spoken_text, markers: e.markers, kind: e.kind, spoken: false },
            ].sort((a, b) => a.index - b.index),
          }));
        case "result":
          return withTurn({ ...state, querySessionId: e.query_session_id ?? state.querySessionId }, e.turn, (turn) => ({
            ...turn,
            answer: { ...fromStreamResult(turn.final ?? "", e.data), contextualizedQuery: null },
          }));
        case "offer":
          return withTurn(state, e.turn, (turn) => ({ ...turn, offer: { kind: e.kind, remaining: e.remaining_sentences } }));
        case "waterfall":
          return withTurn(state, e.turn, (turn) => ({
            ...turn,
            waterfall: {
              legs: e.legs,
              totalFirstAudioMs: e.total_first_audio_ms,
              clientFirstAudioMs: e.client_first_audio_ms,
              speculation: e.speculation,
              waste: e.waste,
              maskUsed: e.mask_used,
            },
          }));
        case "error":
          return { ...state, error: `${e.code}: ${e.message}` };
        default:
          return state;
      }
    }
  }
}

const RESUME_ATTEMPTS = 5;
// Refusals the server makes on purpose. Reconnecting cannot change them, so
// the session ends with the server's own sentence instead of retrying and
// then reporting "Connection lost." (4503: this server cannot run voice.)
const FINAL_ERROR_CODES = new Set(["voice_unavailable", "forbidden", "unauthorized"]);
const FINAL_CLOSE_CODES = new Set([4401, 4403, 4503]);

export function useVoiceSession(options: { querySessionId: string | null }) {
  const token = useToken();
  const client = useQueryClient();
  const [state, dispatch] = useReducer(reduce, initial);

  const socket = useRef<WebSocket | null>(null);
  const audio = useRef<VoiceAudio | null>(null);
  const live = useRef(false);
  const session = useRef<{ id: string; resumeToken: string; querySessionId: string | null } | null>(null);
  const serverState = useRef<VoiceState>("IDLE");
  const turnRef = useRef(0);
  const vad = useRef(new EnergyVad());
  // During playback a murmur must not stop the agent: three loud frames (60 ms).
  const bargeVad = useRef(new EnergyVad(3.5, 0.008, 3));
  const userLevel = useRef<number | null>(null);
  const userLevels = useRef<number[]>([]);
  const firstFrameAt = useRef<Map<string, number>>(new Map());
  const firstLoudAt = useRef<number | null>(null);
  const bargeFiredForTurn = useRef<number | null>(null);
  const earconTimer = useRef<number | null>(null);
  const flushedTurns = useRef<Set<number>>(new Set());
  const wakeLock = useRef<WakeLockSentinel | null>(null);
  const attempts = useRef(0);

  const send = useCallback((message: ClientMessage) => {
    const ws = socket.current;
    if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(message));
  }, []);

  const setServerState = useCallback(
    (next: VoiceState) => {
      const previous = serverState.current;
      serverState.current = next;
      const a = audio.current;
      if (!a) return;
      if (next === "PROCESSING") {
        // Earcon after 800 ms of thinking; never verbal filler.
        if (earconTimer.current !== null) window.clearTimeout(earconTimer.current);
        earconTimer.current = window.setTimeout(() => a.startEarcons(800), 800);
      } else {
        if (earconTimer.current !== null) {
          window.clearTimeout(earconTimer.current);
          earconTimer.current = null;
        }
        a.stopEarcons();
      }
      if (next === "SPEAKING" && previous !== "SPEAKING") {
        vad.current.reset();
        bargeVad.current.reset();
        firstLoudAt.current = null;
        bargeFiredForTurn.current = null;
      }
      if (next === "LISTENING") {
        vad.current.reset();
        firstLoudAt.current = null;
      }
    },
    [],
  );

  const handleEvent = useCallback(
    (event: ServerEvent) => {
      dispatch({ type: "event", event });
      switch (event.type) {
        case "session":
          session.current = {
            id: event.session_id,
            resumeToken: event.resume_token,
            querySessionId: event.query_session_id,
          };
          serverState.current = event.state;
          attempts.current = 0;
          break;
        case "state":
          turnRef.current = event.turn;
          setServerState(event.state);
          if (event.state === "BARGE_IN") flushedTurns.current.add(event.turn);
          break;
        case "final":
          flushedTurns.current.delete(event.turn);
          break;
        case "result":
        case "waterfall":
          void client.invalidateQueries({ queryKey: ["sessions"] });
          void client.invalidateQueries({ queryKey: ["history"] });
          if (event.type === "result" && session.current) {
            if (event.query_session_id) session.current.querySessionId = event.query_session_id;
            void client.invalidateQueries({ queryKey: ["voice-turns", session.current.querySessionId] });
          }
          break;
        case "error":
          if (event.code === "resume_failed") session.current = null;
          if (FINAL_ERROR_CODES.has(event.code)) {
            live.current = false;
            void audio.current?.close();
            audio.current = null;
            dispatch({ type: "status", status: "error", error: event.message });
          }
          break;
        default:
          break;
      }
    },
    [client, setServerState],
  );

  const onFrame = useCallback(
    (frame: { pcm: ArrayBuffer; rms: number; at: number }) => {
      dispatch({ type: "levels", mic: frame.rms });
      const ws = socket.current;
      if (ws && ws.readyState === WebSocket.OPEN) ws.send(frame.pcm);
      const current = serverState.current;
      if (current === "LISTENING" || current === "ENDPOINTING" || current === "CONFIRMING") {
        // Calibrate the user's speaking level for the barge-in threshold.
        if (vad.current.process(frame.rms)) {
          const levels = userLevels.current;
          levels.push(frame.rms);
          if (levels.length > 200) levels.shift();
          const sorted = [...levels].sort((a, b) => a - b);
          userLevel.current = sorted[Math.floor(sorted.length / 2)] ?? null;
        }
        return;
      }
      if (current !== "SPEAKING") return;
      // Client-side barge-in: energy onset above the user's own speech level.
      const loud = frame.rms > bargeVad.current.threshold(userLevel.current);
      if (loud && firstLoudAt.current === null) firstLoudAt.current = frame.at;
      if (!loud) firstLoudAt.current = null;
      const speech = bargeVad.current.process(frame.rms, userLevel.current);
      if (speech && bargeFiredForTurn.current !== turnRef.current) {
        const a = audio.current;
        if (!a) return;
        const flushedAt = a.flush();
        const stopMs = Math.max(0, Math.round((flushedAt - (firstLoudAt.current ?? frame.at)) * 10) / 10);
        bargeFiredForTurn.current = turnRef.current;
        flushedTurns.current.add(turnRef.current);
        dispatch({ type: "barge", turn: turnRef.current, stopMs });
        send({ type: "barge_in", stop_latency_ms: stopMs });
      }
    },
    [send],
  );

  const connect = useCallback(
    async (resume: boolean) => {
      if (!token) return;
      dispatch({ type: "status", status: resume ? "reconnecting" : "connecting" });
      const url = apiUrl("/api/v1/voice/ws").replace(/^http/, "ws");
      const ws = new WebSocket(url);
      ws.binaryType = "arraybuffer";
      socket.current = ws;
      ws.onopen = () => {
        const existing = session.current;
        if (resume && existing) {
          send({ type: "resume", token, session_id: existing.id, resume_token: existing.resumeToken });
        } else {
          send({
            type: "start",
            token,
            query_session_id: existing?.querySessionId ?? options.querySessionId,
            client: {
              sample_rate: 16_000,
              echo_cancellation: audio.current?.echoCancellation ?? false,
              user_agent: navigator.userAgent,
            },
          });
        }
      };
      ws.onmessage = (message: MessageEvent<string | ArrayBuffer>) => {
        if (typeof message.data === "string") {
          const event = parseServerEvent(message.data);
          if (event) handleEvent(event);
          return;
        }
        const frame = unpackAudio(message.data);
        if (!frame || !audio.current) return;
        if (flushedTurns.current.has(frame.turn) && frame.sentence !== 0xffff) return;
        const key = `${frame.turn}:${frame.sentence}`;
        if (!firstFrameAt.current.has(key)) firstFrameAt.current.set(key, performance.now());
        audio.current.enqueue(frame.turn, frame.sentence, frame.pcm);
      };
      ws.onclose = (event: CloseEvent) => {
        if (socket.current === ws) socket.current = null;
        if (FINAL_CLOSE_CODES.has(event.code)) live.current = false;
        if (!live.current) return;
        // Network blip: resume with the session id + token (11A.5).
        if (attempts.current < RESUME_ATTEMPTS) {
          attempts.current += 1;
          const delay = Math.min(500 * 2 ** attempts.current, 5_000);
          window.setTimeout(() => {
            if (live.current) void connect(session.current !== null);
          }, delay);
        } else {
          dispatch({ type: "status", status: "error", error: "Connection lost." });
        }
      };
      ws.onerror = () => {
        /* onclose follows */
      };
    },
    [handleEvent, options.querySessionId, send, token],
  );

  const start = useCallback(async (deviceId?: string) => {
    if (live.current) return;
    live.current = true;
    dispatch({ type: "reset" });
    dispatch({ type: "status", status: "connecting" });
    try {
      const a = new VoiceAudio({
        onFrame,
        onPlaybackStarted: ({ turn, sentence, at }) => {
          dispatch({ type: "spoken", turn, sentence });
          const received = firstFrameAt.current.get(`${turn}:${sentence}`);
          const bufferMs = received !== undefined ? Math.round((at - received) * 10) / 10 : undefined;
          send({ type: "playback", turn, sentence, event: "started", buffer_ms: bufferMs });
        },
        onPlaybackEnded: ({ turn, sentence }) => send({ type: "playback", turn, sentence, event: "ended" }),
        onAgentLevel: (rms) => dispatch({ type: "levels", agent: rms }),
      });
      await a.start(deviceId);
      audio.current = a;
      dispatch({ type: "echo", enabled: a.echoCancellation });
      dispatch({ type: "mic", available: a.micAvailable, error: a.micError });
    } catch (error) {
      live.current = false;
      dispatch({
        type: "status",
        status: "error",
        error: error instanceof Error ? error.message : "Microphone access failed.",
      });
      return;
    }
    try {
      wakeLock.current = await navigator.wakeLock?.request("screen");
    } catch {
      wakeLock.current = null;
    }
    await connect(false);
  }, [connect, onFrame, send]);

  const stop = useCallback(async () => {
    live.current = false;
    send({ type: "stop" });
    socket.current?.close();
    socket.current = null;
    await audio.current?.close();
    audio.current = null;
    session.current = null;
    await wakeLock.current?.release();
    wakeLock.current = null;
    dispatch({ type: "status", status: "idle" });
  }, [send]);

  useEffect(() => {
    const onVisibility = () => {
      if (document.visibilityState === "visible" && live.current) {
        void audio.current?.resume();
        void navigator.wakeLock?.request("screen").then((lock) => {
          wakeLock.current = lock;
        }).catch(() => undefined);
      }
    };
    document.addEventListener("visibilitychange", onVisibility);
    return () => document.removeEventListener("visibilitychange", onVisibility);
  }, []);

  useEffect(() => {
    return () => {
      live.current = false;
      socket.current?.close();
      void audio.current?.close();
      void wakeLock.current?.release();
    };
  }, []);

  const api = useMemo(
    () => ({
      start,
      stop,
      sendText: (text: string) => send({ type: "text", text }),
      confirm: (choice: string) => send({ type: "confirm", choice }),
      continueAnswer: () => send({ type: "continue" }),
      /** Manual interruption from the UI (a tap), measured like a spoken one. */
      interrupt: () => {
        const a = audio.current;
        if (!a || serverState.current !== "SPEAKING") return;
        const t0 = performance.now();
        const flushedAt = a.flush();
        const stopMs = Math.round((flushedAt - t0) * 10) / 10;
        flushedTurns.current.add(turnRef.current);
        dispatch({ type: "barge", turn: turnRef.current, stopMs });
        send({ type: "barge_in", stop_latency_ms: stopMs });
      },
    }),
    [send, start, stop],
  );

  return { state, ...api };
}
