import { Clock, Coins, Cpu, Database } from "lucide-react";

import type { AnswerModel } from "@/components/answer/answer-model";
import { StatusPill } from "@/components/clinical/badges";
import { LocalTime } from "@/components/clinical/local-time";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { formatMs, formatUsd } from "@/lib/text";
import { cn } from "@/lib/utils";

/**
 * Latency honesty (spec #24): every answer says what it cost, how long it
 * took, whether it came from the cache, and exactly which model and prompt
 * versions produced it. Nothing here is estimated — every number is the one
 * the backend recorded on the answer row.
 */
export function AnswerFooter({ answer, className }: { answer: AnswerModel; className?: string }) {
  const tokens =
    answer.inputTokens !== null && answer.outputTokens !== null
      ? `${answer.inputTokens.toLocaleString()} in / ${answer.outputTokens.toLocaleString()} out`
      : null;
  const offline = answer.model === "heuristic" || answer.model === "cache";

  return (
    <footer
      className={cn(
        "flex flex-wrap items-center gap-x-4 gap-y-1.5 border-t pt-3 font-mono text-[11px] text-muted-foreground",
        className,
      )}
      aria-label="Answer provenance"
    >
      <Stat icon={Clock} label="Latency" value={formatMs(answer.latencyMs)} />
      <Stat
        icon={Coins}
        label="Cost"
        value={formatUsd(answer.costUsd)}
        hint={
          tokens
            ? `${tokens} tokens${offline ? " — offline backend, no paid calls" : ""}`
            : offline
              ? "Offline backend — no paid model calls were made"
              : undefined
        }
      />
      {answer.cached && (
        <span className="inline-flex items-center gap-1.5">
          <Database className="size-3" aria-hidden />
          <StatusPill kind="cached" />
          {answer.cacheSavedUsd !== null && answer.cacheSavedUsd > 0 && (
            <span>saved {formatUsd(answer.cacheSavedUsd)}</span>
          )}
        </span>
      )}
      <Stat
        icon={Cpu}
        label="Model"
        value={answer.model ?? "—"}
        hint={
          answer.promptVersion
            ? `Prompt versions: ${answer.promptVersion}`
            : answer.reasoning.generation_mode === "extractive"
              ? "Extractive generation: every sentence is lifted from a cited passage"
              : undefined
        }
      />
      {answer.promptVersion && <span>prompts {answer.promptVersion}</span>}
      {answer.answeredAt && (
        <LocalTime
          iso={answer.answeredAt}
          className="ml-auto"
          options={{ year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }}
        />
      )}
    </footer>
  );
}

function Stat({
  icon: Icon,
  label,
  value,
  hint,
}: {
  icon: typeof Clock;
  label: string;
  value: string;
  hint?: string;
}) {
  const body = (
    <span className="inline-flex items-center gap-1.5">
      <Icon className="size-3" aria-hidden />
      <span className="sr-only">{label}:</span>
      <span className="text-foreground/80">{value}</span>
    </span>
  );
  if (!hint) return body;
  return (
    <Tooltip>
      <TooltipTrigger render={<span className="cursor-help" />}>{body}</TooltipTrigger>
      <TooltipContent>{hint}</TooltipContent>
    </Tooltip>
  );
}
