"use client";

import { useQuery } from "@tanstack/react-query";
import { useCallback, useEffect, useRef, useState } from "react";

import { useOptionalAuth } from "@/components/providers/auth-provider";
import { describeMicError, micConstraints } from "@/components/voice/mic-check";
import type { ChatController } from "@/hooks/use-chat";
import { usePreferences } from "@/hooks/use-preferences";
import { apiFetch } from "@/lib/api";
import type { Schemas } from "@/lib/domain";
import { listenForUtterance, SentenceChunker, SpeechQueue, transcribe } from "@/lib/voice-chat";

export type VoicePhase = "off" | "ready" | "listening" | "transcribing" | "thinking" | "speaking";

/**
 * A spoken conversation with the chat assistant, hands-free:
 *
 *   listen → the question is transcribed and sent as a chat message → the
 *   answer is read aloud as it streams → listen again.
 *
 * Every turn is an ordinary chat turn — the same guardrails, sources and
 * saved conversation as typing. Interrupt cuts an answer short to ask
 * something else; two silent turns in a row pause the loop.
 */
export function useVoiceMode(chat: ChatController, enabled: boolean) {
  const token = useOptionalAuth()?.session?.access_token ?? "";
  const config = useQuery({
    queryKey: ["voice", "config"],
    queryFn: () =>
      apiFetch<Schemas["VoiceConfigOut"]>("/api/v1/voice/config", { accessToken: token }),
    enabled: enabled && Boolean(token),
    staleTime: 5 * 60_000,
  });
  const [phase, setPhase] = useState<VoicePhase>("off");
  const [level, setLevel] = useState(0);
  const [heard, setHeard] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [readAloud, setReadAloud] = useState(true);
  // Settings: the voice and pace answers are read in, and whether the
  // conversation keeps listening after each answer or waits for Talk.
  const { preferences } = usePreferences();

  const chatRef = useRef(chat);
  const readAloudRef = useRef(readAloud);
  const continuousRef = useRef(preferences.voice_continuous);
  useEffect(() => {
    chatRef.current = chat;
    readAloudRef.current = readAloud;
    continuousRef.current = preferences.voice_continuous;
  });
  const active = useRef(false);
  const stream = useRef<MediaStream | null>(null);
  const listening = useRef<AbortController | null>(null);
  const inFlight = useRef(false);
  const queue = useRef<SpeechQueue | null>(null);
  const chunker = useRef<SentenceChunker | null>(null);
  // True from sending a question until its answer has finished streaming.
  const awaiting = useRef(false);
  const knownIds = useRef<Set<string>>(new Set());

  const unavailable =
    config.data?.available === false
      ? (config.data.unavailable_reason ?? "Voice isn't available on this server.")
      : null;

  const releaseMic = () => {
    stream.current?.getTracks().forEach((t) => t.stop());
    stream.current = null;
  };

  const stop = useCallback((message: string | null = null) => {
    active.current = false;
    awaiting.current = false;
    listening.current?.abort();
    queue.current?.stop();
    releaseMic();
    setLevel(0);
    setPhase("off");
    setNotice(message);
  }, []);

  useEffect(() => () => stop(), [stop]);

  /** One question, heard and written down — or null when voice stopped. */
  const captureQuestion = useCallback(async (): Promise<string | null> => {
    let silentTurns = 0;
    while (active.current && stream.current) {
      setPhase("listening");
      const controller = new AbortController();
      listening.current = controller;
      const clip = await listenForUtterance(stream.current, {
        signal: controller.signal,
        onLevel: setLevel,
      });
      if (!active.current || controller.signal.aborted) return null;
      if (!clip) {
        silentTurns += 1;
        if (silentTurns >= 2) {
          active.current = false;
          releaseMic();
          setPhase("ready");
          setNotice("I didn't hear anything, so I've paused. Tap Talk when you're ready.");
          return null;
        }
        continue;
      }
      silentTurns = 0;
      setPhase("transcribing");
      try {
        const text = await transcribe(clip, token);
        if (!active.current) return null;
        if (text) return text;
        setNotice("I couldn't make out any words — try again a little closer to the microphone.");
      } catch (error) {
        setNotice(error instanceof Error ? error.message : "Couldn't transcribe that.");
      }
    }
    return null;
  }, [token]);

  /** After an answer: listen again, or — when Settings says so — wait for Talk. */
  const afterAnswer = useRef<() => void>(() => undefined);

  /** Listen for a question and send it; the answer effect takes it from there. */
  const listen = useCallback(async (): Promise<void> => {
    if (!active.current || inFlight.current) return;
    inFlight.current = true;
    let question: string | null = null;
    try {
      question = await captureQuestion();
    } finally {
      inFlight.current = false;
    }
    if (!question || !active.current) return;
    setNotice(null);
    setHeard(question);
    setPhase("thinking");
    chunker.current = new SentenceChunker();
    knownIds.current = new Set(chatRef.current.messages.map((m) => m.id));
    awaiting.current = true;
    await chatRef.current.send(question);
    // No answer came back at all (the send was refused before streaming).
    if (awaiting.current) {
      awaiting.current = false;
      if (!queue.current?.busy) afterAnswer.current();
    }
  }, [captureQuestion]);

  useEffect(() => {
    afterAnswer.current = () => {
      if (!active.current) return;
      if (continuousRef.current) {
        void listen();
        return;
      }
      active.current = false;
      releaseMic();
      setLevel(0);
      setPhase("ready");
    };
  }, [listen]);

  // Speak the streaming answer a few sentences at a time; listen when done.
  useEffect(() => {
    if (!awaiting.current || !active.current) return;
    const answer = [...chat.messages]
      .reverse()
      .find((m) => m.role === "assistant" && !knownIds.current.has(m.id));
    if (!answer || answer.role !== "assistant") return;
    const finished = answer.status !== "streaming";
    let text = answer.text;
    if (answer.status === "blocked") text = answer.blocked?.message ?? "";
    if (answer.status === "error") text = "Sorry — I couldn't answer that. Please try again.";
    if (answer.escalation) text = `${answer.escalation}\n\n${text}`;
    const chunks = readAloudRef.current ? (chunker.current?.take(text, finished) ?? []) : [];
    for (const chunk of chunks) queue.current?.enqueue(chunk);
    if (chunks.length) setPhase("speaking");
    if (finished) {
      awaiting.current = false;
      if (!queue.current?.busy) afterAnswer.current();
    }
  }, [chat.messages]);

  const start = useCallback(async () => {
    if (active.current) return;
    setNotice(null);
    if (unavailable) {
      setPhase("ready");
      setNotice(unavailable);
      return;
    }
    if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === "undefined") {
      setPhase("ready");
      setNotice("This browser can't record audio. Use a current Chrome, Edge, Safari or Firefox.");
      return;
    }
    try {
      stream.current = await navigator.mediaDevices.getUserMedia({ audio: micConstraints() });
    } catch (error) {
      setPhase("ready");
      setNotice(describeMicError(error));
      return;
    }
    active.current = true;
    queue.current = new SpeechQueue(
      token,
      {
        onLevel: setLevel,
        onIdle: () => {
          // The answer has been heard in full: the floor is the person's again.
          if (active.current && !awaiting.current) afterAnswer.current();
        },
      },
      { voice: preferences.voice_name, rate: preferences.voice_rate },
    );
    void listen();
  }, [listen, preferences.voice_name, preferences.voice_rate, token, unavailable]);

  /** Cut the answer short and listen for the next question. */
  const interrupt = useCallback(() => {
    awaiting.current = false;
    queue.current?.stop();
    if (chatRef.current.busy) chatRef.current.stop();
    void listen();
  }, [listen]);

  return {
    phase,
    level,
    heard,
    notice,
    readAloud,
    setReadAloud,
    unavailable,
    start,
    stop,
    interrupt,
    /** Show the voice bar without starting the microphone. */
    open: () => {
      setNotice(null);
      setPhase((p) => (p === "off" ? "ready" : p));
    },
  };
}

export type VoiceMode = ReturnType<typeof useVoiceMode>;
