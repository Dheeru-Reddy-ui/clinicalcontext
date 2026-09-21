"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useCallback, useReducer, useRef } from "react";

import { fromStreamResult, type AnswerModel } from "@/components/answer/answer-model";
import { useToken } from "@/hooks/use-api";
import type { ReasoningStage, StreamEvent } from "@/lib/domain";
import { streamQuery, StreamError, type QueryRequest } from "@/lib/stream";

export interface ReasoningStep {
  stage: ReasoningStage | "accepted" | "escalation";
  message: string;
  data: Record<string, unknown>;
  at: number;
}

export type AskPhase = "idle" | "streaming" | "done" | "blocked" | "error";

export interface AskState {
  phase: AskPhase;
  query: string;
  queryId: string | null;
  sessionId: string | null;
  contextualizedQuery: string | null;
  steps: ReasoningStep[];
  /** Grows token by token; the final answer is authoritative once `answer` is set. */
  partialText: string;
  answer: AnswerModel | null;
  escalationBanner: string | null;
  blocked: { by: string | null; code: string | null; message: string } | null;
  error: { message: string; code: string; retryAfter: number | null } | null;
  startedAt: number | null;
  finishedAt: number | null;
}

type Action =
  | { type: "start"; query: string; sessionId: string | null }
  | { type: "event"; event: StreamEvent }
  | { type: "error"; error: AskState["error"] }
  | { type: "reset" };

const initial: AskState = {
  phase: "idle",
  query: "",
  queryId: null,
  sessionId: null,
  contextualizedQuery: null,
  steps: [],
  partialText: "",
  answer: null,
  escalationBanner: null,
  blocked: null,
  error: null,
  startedAt: null,
  finishedAt: null,
};

function reduce(state: AskState, action: Action): AskState {
  switch (action.type) {
    case "start":
      return {
        ...initial,
        phase: "streaming",
        query: action.query,
        sessionId: action.sessionId,
        startedAt: Date.now(),
      };
    case "reset":
      return initial;
    case "error":
      return { ...state, phase: "error", error: action.error, finishedAt: Date.now() };
    case "event": {
      const e = action.event;
      switch (e.stage) {
        case "accepted":
          return {
            ...state,
            queryId: e.data.query_id,
            sessionId: e.data.session_id,
            contextualizedQuery:
              e.data.contextualized_query !== state.query ? e.data.contextualized_query : null,
            steps: [...state.steps, { stage: "accepted", message: e.message, data: {}, at: Date.now() }],
          };
        case "escalation":
          return {
            ...state,
            escalationBanner: e.message,
            steps: [...state.steps, { stage: "escalation", message: e.message, data: {}, at: Date.now() }],
          };
        case "blocked":
          return {
            ...state,
            phase: "blocked",
            blocked: { by: e.data.blocked_by, code: e.data.code, message: e.message },
            finishedAt: Date.now(),
          };
        case "token":
          return { ...state, partialText: state.partialText + e.data.text };
        case "result":
          return {
            ...state,
            phase: "done",
            answer: {
              ...fromStreamResult(state.query, e.data),
              contextualizedQuery: state.contextualizedQuery,
            },
            partialText: e.data.answer,
            finishedAt: Date.now(),
          };
        case "error":
          return {
            ...state,
            phase: "error",
            error: { message: e.message, code: e.data.type, retryAfter: null },
            finishedAt: Date.now(),
          };
        default:
          return {
            ...state,
            steps: [
              ...state.steps,
              { stage: e.stage, message: e.message, data: e.data as Record<string, unknown>, at: Date.now() },
            ],
          };
      }
    }
  }
}

/**
 * The streaming state machine behind the Ask screen.
 *
 * One request at a time; starting a new one aborts the previous fetch (not
 * just the render). On completion the history/session caches are invalidated
 * so the sidebar and lists reflect the new turn without a manual refresh.
 */
export function useAsk() {
  const token = useToken();
  const client = useQueryClient();
  const [state, dispatch] = useReducer(reduce, initial);
  const controller = useRef<AbortController | null>(null);

  const cancel = useCallback(() => {
    controller.current?.abort();
    controller.current = null;
  }, []);

  const ask = useCallback(
    async (request: QueryRequest) => {
      cancel();
      const ac = new AbortController();
      controller.current = ac;
      dispatch({ type: "start", query: request.query, sessionId: request.session_id ?? null });
      try {
        for await (const event of streamQuery(request, token, {
          signal: ac.signal,
          idempotencyKey: crypto.randomUUID(),
        })) {
          if (ac.signal.aborted) return;
          dispatch({ type: "event", event });
        }
      } catch (error) {
        if (ac.signal.aborted) return;
        if (error instanceof StreamError) {
          dispatch({
            type: "error",
            error: { message: error.message, code: error.code, retryAfter: error.retryAfter },
          });
        } else {
          dispatch({
            type: "error",
            error: {
              message: error instanceof Error ? error.message : "The request failed.",
              code: "network",
              retryAfter: null,
            },
          });
        }
      } finally {
        if (controller.current === ac) controller.current = null;
        void client.invalidateQueries({ queryKey: ["history"] });
        void client.invalidateQueries({ queryKey: ["sessions"] });
        if (request.session_id) {
          void client.invalidateQueries({ queryKey: ["session", request.session_id] });
        }
      }
    },
    [cancel, client, token],
  );

  const reset = useCallback(() => {
    cancel();
    dispatch({ type: "reset" });
  }, [cancel]);

  return { state, ask, cancel, reset };
}
