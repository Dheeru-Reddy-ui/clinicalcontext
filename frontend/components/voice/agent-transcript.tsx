"use client";

import { AlertTriangle, ShieldCheck, Sparkles, Volume2 } from "lucide-react";
import { useState } from "react";

import { CitationPanel } from "@/components/answer/citation-panel";
import { ConfidenceBadge, GradeBadge } from "@/components/clinical/badges";
import { Button } from "@/components/ui/button";
import type { AgentSentence, VoiceTurn } from "@/hooks/use-voice-session";
import { stanceByMarker } from "@/lib/domain";
import { cn } from "@/lib/utils";

/**
 * The agent's side of a turn (11G.1–11G.3): sentences appear as they are
 * synthesized (dimmed) and light up as they are actually *played*, with the
 * citation chips of each sentence arriving at that moment — the screen
 * carries the precision, the voice carries the gist. Guardrail lines keep
 * the Phase 10 block / refusal / escalation treatment; the red-flag line is
 * spoken first, and shown first.
 */
export function AgentTranscript({
  turn,
  onContinue,
  className,
}: {
  turn: VoiceTurn;
  onContinue?: () => void;
  className?: string;
}) {
  const [activeMarker, setActiveMarker] = useState<number | null>(null);
  const answer = turn.answer;
  const stances = answer ? stanceByMarker(answer.contradiction, answer.citations) : undefined;
  const spokenIndexes = new Set(turn.sentences.filter((s) => s.spoken).map((s) => s.index));

  if (turn.sentences.length === 0 && !turn.guardrail) return null;

  return (
    <div className={cn("space-y-3", className)} data-testid={`agent-turn-${turn.index}`}>
      {turn.guardrail?.verdict === "escalation" && (
        <div className="flex items-start gap-2 rounded-md border border-redflag/40 bg-redflag-bg p-3 text-sm text-redflag-fg" role="alert">
          <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden />
          <span>{turn.guardrail.spoken}</span>
        </div>
      )}
      {turn.guardrail?.verdict === "blocked" && (
        <div className="flex items-start gap-2 rounded-md border border-guard-calm-border bg-guard-calm-bg p-3 text-sm text-guard-calm-fg" role="status">
          <ShieldCheck className="mt-0.5 size-4 shrink-0" aria-hidden />
          <div>
            <p className="font-medium">
              {turn.guardrail.blockedBy === "phi" ? "Stopped: patient information" : "Out of scope"}
            </p>
            <p className="opacity-90">{turn.guardrail.spoken}</p>
          </div>
        </div>
      )}

      <div className="flex items-start gap-3">
        <span className="mt-1 grid size-7 shrink-0 place-items-center rounded-full bg-citation-bg text-citation-fg">
          <Volume2 className="size-3.5" aria-hidden />
        </span>
        <div className="min-w-0 flex-1 space-y-1">
          {turn.sentences
            .filter((s) => s.kind !== "escalation" && s.kind !== "refusal")
            .map((s) => (
              <Sentence key={s.index} sentence={s} spoken={spokenIndexes.has(s.index)} onCite={setActiveMarker} />
            ))}
          {turn.offer && (
            <div className="flex flex-wrap items-center gap-2 pt-1">
              <span className="text-xs text-muted-foreground">
                {turn.offer.remaining} more {turn.offer.remaining === 1 ? "sentence" : "sentences"} — say “yes” or
              </span>
              {onContinue && (
                <Button size="sm" variant="outline" onClick={onContinue}>
                  <Sparkles /> Go on
                </Button>
              )}
            </div>
          )}
        </div>
      </div>

      {answer && (
        <div className="ml-10 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
          {answer.confidence && <ConfidenceBadge level={answer.confidence} />}
          <GradeBadge grade={answer.evidenceGrade} />
          {answer.abstained && <span>abstained</span>}
          {answer.contradiction.detected && <span className="text-conflict-fg">sources disagree</span>}
          <span>{answer.citations.length} sources</span>
        </div>
      )}

      {answer && activeMarker !== null && (
        <CitationPanel
          citations={answer.citations}
          answer={answer.content}
          activeMarker={activeMarker}
          onChange={setActiveMarker}
          stances={stances}
          className="ml-10"
        />
      )}
    </div>
  );
}

function Sentence({
  sentence,
  spoken,
  onCite,
}: {
  sentence: AgentSentence;
  spoken: boolean;
  onCite: (marker: number) => void;
}) {
  const tone =
    sentence.kind === "abstention"
      ? "text-foreground/90 italic"
      : sentence.kind === "offer" || sentence.kind === "mask" || sentence.kind === "system"
        ? "text-muted-foreground"
        : sentence.kind === "confirmation"
          ? "text-conflict-fg"
          : "text-foreground";
  const text = sentence.text.replace(/\s*\[\d+(?:,\s*\d+)*\]/g, "");
  return (
    <p
      className={cn("text-base leading-7 transition-opacity", tone, spoken ? "opacity-100" : "opacity-40")}
      data-spoken={spoken ? "true" : "false"}
      data-kind={sentence.kind}
    >
      {text}
      {spoken &&
        sentence.markers.map((m) => (
          <button
            key={m}
            type="button"
            className="cite-chip ml-1 align-baseline"
            onClick={() => onCite(m)}
            aria-label={`Open source ${m}`}
          >
            {m}
          </button>
        ))}
    </p>
  );
}
