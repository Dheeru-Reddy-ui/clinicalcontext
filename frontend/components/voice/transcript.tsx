"use client";

import { Keyboard, Mic } from "lucide-react";

import type { VoiceTurn } from "@/hooks/use-voice-session";
import { cn } from "@/lib/utils";

/**
 * The user's side of the conversation (11G.1): partials in grey as the
 * recognizer hears them, the final in full colour, and every correction the
 * medical-term pass made shown inline — the clinician watches themselves
 * being understood, and sees exactly what the system changed.
 */
export function UserTranscript({ turn, className }: { turn: VoiceTurn; className?: string }) {
  const corrected = new Map(turn.corrections.map((c) => [c.corrected.toLowerCase(), c]));
  const words = turn.final ? turn.final.split(/\s+/) : [];
  return (
    <div className={cn("flex items-start gap-3", className)} data-testid={`user-turn-${turn.index}`}>
      <span className="mt-1 grid size-7 shrink-0 place-items-center rounded-full bg-primary/10 text-primary">
        {turn.source === "text" ? <Keyboard className="size-3.5" aria-hidden /> : <Mic className="size-3.5" aria-hidden />}
      </span>
      <div className="min-w-0 flex-1">
        {turn.final ? (
          <p className="text-base leading-7">
            {words.map((word, i) => {
              const key = word.toLowerCase().replace(/[.,?!;:]+$/, "");
              const fix = corrected.get(key);
              return (
                <span key={i}>
                  {fix ? (
                    <span
                      className="rounded-sm bg-citation-bg px-1 text-citation-fg underline decoration-dotted"
                      title={`Heard “${fix.original}” — corrected against the corpus vocabulary (${fix.score})`}
                    >
                      {word}
                    </span>
                  ) : (
                    word
                  )}
                  {i < words.length - 1 ? " " : ""}
                </span>
              );
            })}
          </p>
        ) : turn.partial ? (
          <p className="text-base leading-7 text-muted-foreground" aria-live="polite">
            {turn.partialWords.length > 0
              ? turn.partialWords.map((w, i) => (
                  <span key={i} className={cn(w.confidence < 0.5 && "opacity-60")}>
                    {w.text}{" "}
                  </span>
                ))
              : turn.partial}
            <span className="ml-1 inline-block h-4 w-0.5 animate-pulse bg-muted-foreground align-middle" />
          </p>
        ) : null}
        {turn.corrections.length > 0 && (
          <p className="mt-1 text-xs text-muted-foreground">
            Heard {turn.corrections.map((c) => `“${c.original}” → ${c.corrected}`).join(", ")}
          </p>
        )}
        {turn.endpoint && (
          <p className="mt-0.5 font-mono text-[11px] text-muted-foreground">
            endpoint · {turn.endpoint.layer} · {Math.round(turn.endpoint.decisionMs)} ms
          </p>
        )}
      </div>
    </div>
  );
}
