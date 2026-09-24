"use client";

import { Loader2, Mic, Square } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { describeMicError } from "@/components/voice/mic-check";
import { Button } from "@/components/ui/button";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { useToken, useVoiceConfig } from "@/hooks/use-api";
import { apiUrl } from "@/lib/api";
import { cn } from "@/lib/utils";

const MAX_SECONDS = 60;

function pickMimeType(): string {
  const candidates = ["audio/webm;codecs=opus", "audio/webm", "audio/ogg;codecs=opus", "audio/mp4"];
  for (const type of candidates) {
    if (typeof MediaRecorder !== "undefined" && MediaRecorder.isTypeSupported(type)) return type;
  }
  return "";
}

/**
 * Voice typing: tap, speak, tap again — the words appear in the message box
 * to check before sending. Deepgram's medical model does the listening (the
 * same one voice mode uses), so drug names come through; on a server without
 * it the button says why instead of recording into nothing.
 */
export function DictationButton({ onText }: { onText: (text: string) => void }) {
  const token = useToken();
  const config = useVoiceConfig();
  const [state, setState] = useState<"idle" | "recording" | "working">("idle");
  const [seconds, setSeconds] = useState(0);
  const [message, setMessage] = useState<string | null>(null);
  const recorder = useRef<MediaRecorder | null>(null);
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);

  const unavailable =
    config.data?.available === false
      ? config.data.unavailable_reason ?? "Voice typing isn't available on this server."
      : null;

  useEffect(
    () => () => {
      if (timer.current) clearInterval(timer.current);
      recorder.current?.stream.getTracks().forEach((t) => t.stop());
    },
    [],
  );

  const stop = () => {
    if (timer.current) clearInterval(timer.current);
    timer.current = null;
    if (recorder.current?.state === "recording") recorder.current.stop();
  };

  const start = async () => {
    setMessage(null);
    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      });
    } catch (error) {
      setMessage(describeMicError(error));
      return;
    }
    const mimeType = pickMimeType();
    const rec = new MediaRecorder(stream, mimeType ? { mimeType } : undefined);
    const chunks: BlobPart[] = [];
    rec.ondataavailable = (e) => e.data.size && chunks.push(e.data);
    rec.onstop = async () => {
      stream.getTracks().forEach((t) => t.stop());
      const type = (rec.mimeType || "audio/webm").split(";")[0] ?? "audio/webm";
      const blob = new Blob(chunks, { type });
      if (blob.size < 1200) {
        setState("idle");
        setMessage("That was too short — hold on a little longer.");
        return;
      }
      setState("working");
      try {
        const response = await fetch(apiUrl("/api/v1/voice/transcribe"), {
          method: "POST",
          headers: { "Content-Type": type, Authorization: `Bearer ${token}` },
          body: blob,
        });
        const body = (await response.json().catch(() => ({}))) as {
          text?: string;
          error?: { message?: string };
        };
        if (!response.ok) throw new Error(body.error?.message ?? "Couldn't transcribe that.");
        if (body.text) onText(body.text);
        else setMessage("I didn't catch any words — try again closer to the microphone.");
      } catch (error) {
        setMessage(error instanceof Error ? error.message : "Couldn't transcribe that.");
      } finally {
        setState("idle");
      }
    };
    recorder.current = rec;
    rec.start(250);
    setSeconds(0);
    setState("recording");
    timer.current = setInterval(() => {
      setSeconds((s) => {
        if (s + 1 >= MAX_SECONDS) stop();
        return s + 1;
      });
    }, 1000);
  };

  const label =
    state === "recording" ? "Stop and transcribe" : unavailable ? unavailable : "Speak instead of typing";

  return (
    <span className="relative inline-flex items-center">
      <Tooltip>
        <TooltipTrigger
          render={
            <Button
              type="button"
              size="icon"
              variant={state === "recording" ? "destructive" : "ghost"}
              aria-label={label}
              disabled={!token || state === "working" || Boolean(unavailable)}
              onClick={() => (state === "recording" ? stop() : void start())}
              data-testid="chat-dictate"
              className={cn(state === "recording" && "animate-pulse")}
            />
          }
        >
          {state === "working" ? (
            <Loader2 className="animate-spin" />
          ) : state === "recording" ? (
            <Square className="size-3.5 fill-current" />
          ) : (
            <Mic />
          )}
        </TooltipTrigger>
        <TooltipContent className="max-w-xs">{label}</TooltipContent>
      </Tooltip>
      {state === "recording" && (
        <span className="ml-1 font-mono text-xs text-destructive" aria-live="polite">
          0:{String(seconds).padStart(2, "0")}
        </span>
      )}
      {message && (
        <span
          role="status"
          className="absolute right-0 bottom-10 z-10 w-64 rounded-md border bg-popover p-2 text-xs shadow"
          onClick={() => setMessage(null)}
        >
          {message}
        </span>
      )}
    </span>
  );
}
