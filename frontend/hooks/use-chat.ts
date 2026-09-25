"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useRef, useState } from "react";

import { useOptionalAuth } from "@/components/providers/auth-provider";
import { usePreferences } from "@/hooks/use-preferences";
import { apiFetch } from "@/lib/api";
import {
  asChatResult,
  ChatError,
  streamChat,
  streamPublicChat,
  type Audience,
  type ChatEvent,
  type ChatKind,
  type ChatResult,
  type PublicTurn,
} from "@/lib/chat";

export interface UserMessage {
  id: string;
  role: "user";
  text: string;
}

export interface AssistantMessage {
  id: string;
  role: "assistant";
  status: "streaming" | "done" | "blocked" | "error";
  /** Grows token by token; replaced by the result's answer when it arrives. */
  text: string;
  steps: Array<{ key: string; message: string }>;
  result: ChatResult | null;
  escalation: string | null;
  blocked: { message: string; code: string } | null;
  error: string | null;
}

export type ChatMessage = UserMessage | AssistantMessage;

export interface UseChatOptions {
  /** "app" stores the conversation; "public" (the website) stores nothing. */
  mode?: "app" | "public";
  kind?: ChatKind;
  audience?: Audience;
  specialty?: string | null;
  level?: "mbbs" | "pg" | null;
  sessionId?: string | null;
}

interface SessionDetail {
  id: string;
  turns: Array<{
    query_id: string;
    question: string;
    audience: string | null;
    status: string;
    answer: string | null;
    answer_id: string | null;
    citations: unknown[];
    details: Record<string, unknown>;
  }>;
}

let counter = 0;
const nextId = () => `m${Date.now().toString(36)}${(counter++).toString(36)}`;

function applyEvent(message: AssistantMessage, event: ChatEvent): AssistantMessage {
  switch (event.stage) {
    case "token":
      return { ...message, text: message.text + event.text };
    case "progress":
      return { ...message, steps: [...message.steps, { key: event.key, message: event.message }] };
    case "escalation":
      return { ...message, escalation: event.message };
    case "blocked":
      return { ...message, status: "blocked", blocked: { message: event.message, code: event.code } };
    case "result":
      return { ...message, status: "done", text: event.result.answer, result: event.result };
    case "error":
      return { ...message, status: "error", error: event.message };
    default:
      return message;
  }
}

/**
 * One conversation with the assistant: messages, streaming, stop, and —
 * signed in — the stored conversation it continues.
 */
export function useChat(options: UseChatOptions = {}) {
  const { mode = "app", kind = "chat", specialty = null, level = null } = options;
  // Optional: the website's chatbot runs outside the signed-in app.
  const token = useOptionalAuth()?.session?.access_token ?? "";
  const queryClient = useQueryClient();
  // Who answers are written for: the screen's own choice (Learn, prescribing
  // support), else the person's Settings default — until they switch it in
  // this conversation, which then wins until the next new conversation.
  const { preferences } = usePreferences();
  const preferred: Audience = options.audience ?? (mode === "app" ? preferences.audience : "patient");
  const [audience, setAudienceState] = useState<Audience>(preferred);
  const chosen = useRef(false);
  useEffect(() => {
    if (!chosen.current) setAudienceState(preferred);
  }, [preferred]);
  const setAudience = useCallback((next: Audience) => {
    chosen.current = true;
    setAudienceState(next);
  }, []);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [sessionId, setSessionId] = useState<string | null>(options.sessionId ?? null);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(false);
  const abort = useRef<AbortController | null>(null);

  useEffect(() => () => abort.current?.abort(), []);

  const update = useCallback((id: string, fn: (m: AssistantMessage) => AssistantMessage) => {
    setMessages((all) =>
      all.map((m) => (m.id === id && m.role === "assistant" ? fn(m) : m)),
    );
  }, []);

  const send = useCallback(
    async (text: string) => {
      const message = text.trim();
      if (!message || busy) return;
      if (mode === "app" && !token) return;
      const assistantId = nextId();
      const history: PublicTurn[] = [];
      for (let i = 0; i < messages.length - 1; i += 1) {
        const q = messages[i];
        const a = messages[i + 1];
        if (q?.role === "user" && a?.role === "assistant" && a.status === "done") {
          history.push({ question: q.text, answer: a.text.slice(0, 5000) });
        }
      }
      setMessages((all) => [
        ...all,
        { id: nextId(), role: "user", text: message },
        {
          id: assistantId,
          role: "assistant",
          status: "streaming",
          text: "",
          steps: [],
          result: null,
          escalation: null,
          blocked: null,
          error: null,
        },
      ]);
      setBusy(true);
      const controller = new AbortController();
      abort.current = controller;
      try {
        const events =
          mode === "public"
            ? streamPublicChat(message, audience, history, controller.signal)
            : streamChat(
                { message, audience, session_id: sessionId, kind, specialty, level },
                token,
                controller.signal,
              );
        for await (const event of events) {
          if (event.stage === "accepted" && event.sessionId) setSessionId(event.sessionId);
          update(assistantId, (m) => applyEvent(m, event));
        }
        update(assistantId, (m) =>
          m.status === "streaming"
            ? { ...m, status: m.text ? "done" : "error", error: m.text ? null : "No answer came back." }
            : m,
        );
        if (mode === "app") void queryClient.invalidateQueries({ queryKey: ["chat-sessions"] });
      } catch (error) {
        if (controller.signal.aborted) {
          update(assistantId, (m) => ({ ...m, status: "done", text: m.text || "(stopped)" }));
        } else {
          const text =
            error instanceof ChatError
              ? error.message
              : "Couldn't reach the assistant. The server may be waking up — try again in a minute.";
          update(assistantId, (m) => ({ ...m, status: "error", error: text }));
        }
      } finally {
        setBusy(false);
        abort.current = null;
      }
    },
    [audience, busy, kind, level, messages, mode, queryClient, sessionId, specialty, token, update],
  );

  const stop = useCallback(() => abort.current?.abort(), []);

  const reset = useCallback(() => {
    abort.current?.abort();
    setMessages([]);
    setSessionId(null);
    chosen.current = false;
    setAudienceState(preferred);
  }, [preferred]);

  const open = useCallback(
    async (id: string) => {
      if (!token) return;
      abort.current?.abort();
      setLoading(true);
      try {
        const detail = await apiFetch<SessionDetail>(`/api/v1/chat/sessions/${id}`, {
          accessToken: token,
        });
        const restored: ChatMessage[] = [];
        for (const turn of detail.turns) {
          restored.push({ id: `q${turn.query_id}`, role: "user", text: turn.question });
          const details = (turn.details?.chat ?? {}) as Record<string, unknown>;
          const blocked = turn.status === "blocked";
          restored.push({
            id: `a${turn.query_id}`,
            role: "assistant",
            status: blocked ? "blocked" : turn.answer ? "done" : "error",
            text: turn.answer ?? "",
            steps: [],
            result: turn.answer
              ? asChatResult({
                  ...details,
                  answer: turn.answer,
                  answer_id: turn.answer_id,
                  query_id: turn.query_id,
                  session_id: detail.id,
                  citations: turn.citations,
                })
              : null,
            escalation: typeof details.escalation === "string" ? details.escalation : null,
            blocked: blocked
              ? { message: "This message wasn't answered (it was stopped by a safety check).", code: "blocked" }
              : null,
            error: !blocked && !turn.answer ? "This question didn't get an answer." : null,
          });
        }
        setMessages(restored);
        setSessionId(detail.id);
        const last = detail.turns[detail.turns.length - 1];
        if (last?.audience === "patient" || last?.audience === "clinician" || last?.audience === "student") {
          // Reopening a conversation keeps the voice it was held in.
          chosen.current = true;
          setAudienceState(last.audience);
        }
      } finally {
        setLoading(false);
      }
    },
    [token],
  );

  const ready = mode === "public" || Boolean(token);
  return { messages, send, stop, reset, open, busy, loading, sessionId, audience, setAudience, ready };
}

export type ChatController = ReturnType<typeof useChat>;
