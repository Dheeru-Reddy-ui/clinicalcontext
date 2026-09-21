"use client";

import { Ear, HelpCircle, Loader2, MicOff, Volume2, Zap } from "lucide-react";

import type { VoiceState } from "@/lib/voice/protocol";
import { cn } from "@/lib/utils";

/**
 * The unmissable state indicator (11G.1): what the pipeline is doing right
 * now, in one word, big enough to read across a room.
 */
const LABELS: Record<VoiceState, { label: string; hint: string; icon: typeof Ear; tone: string }> = {
  IDLE: { label: "Off", hint: "Tap the microphone to start", icon: MicOff, tone: "bg-muted text-muted-foreground" },
  LISTENING: { label: "Listening", hint: "Ask a clinical question", icon: Ear, tone: "bg-primary/10 text-primary ring-2 ring-primary/40" },
  ENDPOINTING: { label: "Listening", hint: "Take your time", icon: Ear, tone: "bg-primary/10 text-primary ring-2 ring-primary/20" },
  PROCESSING: { label: "Thinking", hint: "Checking the literature", icon: Loader2, tone: "bg-amber-500/10 text-amber-700 dark:text-amber-300" },
  SPEAKING: { label: "Speaking", hint: "Speak to interrupt", icon: Volume2, tone: "bg-citation-bg text-citation-fg" },
  BARGE_IN: { label: "Interrupted", hint: "Go ahead", icon: Zap, tone: "bg-conflict-bg text-conflict-fg" },
  CONFIRMING: { label: "Confirming", hint: "Say or tap which one you meant", icon: HelpCircle, tone: "bg-conflict-bg text-conflict-fg ring-2 ring-conflict/40" },
  CLOSED: { label: "Ended", hint: "Session closed", icon: MicOff, tone: "bg-muted text-muted-foreground" },
};

export function StateIndicator({ state, className }: { state: VoiceState; className?: string }) {
  const meta = LABELS[state];
  const Icon = meta.icon;
  return (
    <div
      className={cn("flex items-center gap-3 rounded-full px-4 py-2 transition-colors", meta.tone, className)}
      role="status"
      aria-live="polite"
      data-voice-state={state}
    >
      <Icon className={cn("size-5", state === "PROCESSING" && "animate-spin")} aria-hidden />
      <div className="leading-tight">
        <div className="text-base font-semibold">{meta.label}</div>
        <div className="text-xs opacity-80">{meta.hint}</div>
      </div>
    </div>
  );
}
