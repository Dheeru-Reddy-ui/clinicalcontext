"use client";

import { AudioLines, Loader2, Mic, MicOff, Square, Volume2, VolumeX, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import type { VoiceMode } from "@/hooks/use-voice-mode";
import { cn } from "@/lib/utils";

const PHASE_TEXT: Record<Exclude<VoiceMode["phase"], "off">, string> = {
  ready: "Voice mode — tap Talk and ask your question out loud.",
  listening: "Listening… ask your question. I'll answer when you pause.",
  transcribing: "Got it — writing down what you said…",
  thinking: "Looking at the evidence…",
  speaking: "Speaking — tap Interrupt to ask something else.",
};

/** The button in the message box that starts (or ends) a spoken conversation. */
export function VoiceModeButton({ voice }: { voice: VoiceMode }) {
  const on = voice.phase !== "off" && voice.phase !== "ready";
  const label = on ? "End voice conversation" : voice.unavailable ?? "Talk to the assistant (voice mode)";
  return (
    <Tooltip>
      <TooltipTrigger
        render={
          <Button
            type="button"
            size="icon"
            variant={on ? "secondary" : "ghost"}
            aria-label={on ? "End voice conversation" : "Start voice conversation"}
            aria-pressed={on}
            onClick={() => (on ? voice.stop() : void voice.start())}
            data-testid="voice-mode"
          />
        }
      >
        <AudioLines className={cn(on && "text-primary")} />
      </TooltipTrigger>
      <TooltipContent className="max-w-xs">{label}</TooltipContent>
    </Tooltip>
  );
}

/**
 * What the voice conversation is doing right now, above the message box:
 * listening (with the microphone's level), writing down, thinking or
 * speaking — and the controls to talk, interrupt, mute the reading and end.
 */
export function VoiceBar({ voice }: { voice: VoiceMode }) {
  if (voice.phase === "off") return null;
  const phase = voice.phase;
  const busy = phase === "transcribing" || phase === "thinking";
  const bars = [0.55, 0.8, 1, 0.8, 0.55];

  return (
    <div
      className="mb-2 flex flex-wrap items-center gap-3 rounded-2xl border border-primary/30 bg-primary/5 px-3 py-2.5"
      role="status"
      aria-live="polite"
      data-testid="voice-bar"
      data-phase={phase}
    >
      <span
        aria-hidden
        className={cn(
          "grid size-10 shrink-0 place-items-center rounded-full",
          phase === "listening" ? "bg-primary text-primary-foreground" : "bg-primary/15 text-primary",
        )}
      >
        {busy ? (
          <Loader2 className="size-4 animate-spin" />
        ) : phase === "speaking" ? (
          <Volume2 className="size-4" />
        ) : phase === "listening" ? (
          <Mic className="size-4" />
        ) : (
          <MicOff className="size-4" />
        )}
      </span>

      <div className="min-w-0 flex-1">
        <p className="text-sm font-medium">{PHASE_TEXT[phase]}</p>
        {voice.notice ? (
          <p className="text-xs text-amber-700 dark:text-amber-300" data-testid="voice-notice">
            {voice.notice}
          </p>
        ) : voice.heard && phase !== "listening" ? (
          <p className="truncate text-xs text-muted-foreground">You said: “{voice.heard}”</p>
        ) : null}
      </div>

      {(phase === "listening" || phase === "speaking") && (
        <div className="flex h-6 items-center gap-0.5" aria-hidden>
          {bars.map((weight, i) => (
            <span
              key={i}
              className="w-1 rounded-full bg-primary transition-[height] duration-100"
              style={{ height: `${Math.max(12, Math.min(100, voice.level * 100 * weight + 12))}%` }}
            />
          ))}
        </div>
      )}

      <div className="flex items-center gap-1">
        {phase === "ready" && (
          <Button size="sm" onClick={() => void voice.start()} data-testid="voice-talk">
            <Mic /> Talk
          </Button>
        )}
        {(phase === "speaking" || phase === "thinking") && (
          <Button size="sm" variant="outline" onClick={voice.interrupt} data-testid="voice-interrupt">
            <Square className="size-3 fill-current" /> Interrupt
          </Button>
        )}
        <Button
          size="icon-sm"
          variant="ghost"
          onClick={() => voice.setReadAloud(!voice.readAloud)}
          aria-label={voice.readAloud ? "Stop reading answers aloud" : "Read answers aloud"}
          aria-pressed={voice.readAloud}
          title={voice.readAloud ? "Answers are read aloud" : "Answers are not read aloud"}
        >
          {voice.readAloud ? <Volume2 /> : <VolumeX />}
        </Button>
        <Button
          size="icon-sm"
          variant="ghost"
          onClick={() => voice.stop()}
          aria-label="End voice conversation"
          data-testid="voice-end"
        >
          <X />
        </Button>
      </div>
    </div>
  );
}
