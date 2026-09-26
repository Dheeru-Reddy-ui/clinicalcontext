"use client";

import {
  AlertTriangle,
  Check,
  FileSearch,
  GitBranch,
  Loader2,
  PenLine,
  Scale,
  Search,
  ShieldCheck,
  Sparkles,
  Split,
  XCircle,
} from "lucide-react";

import type { ReasoningStep } from "@/hooks/use-ask";
import { cn } from "@/lib/utils";

const ICONS: Record<string, typeof Search> = {
  accepted: Check,
  classifying: GitBranch,
  decomposing: Split,
  searching: Search,
  searched: FileSearch,
  grading: Scale,
  rewriting: PenLine,
  checking_conflict: Scale,
  conflict_found: AlertTriangle,
  generating: Sparkles,
  verifying: ShieldCheck,
  grounding_failed: XCircle,
  grounding_pruned: ShieldCheck,
  grounding_fallback: ShieldCheck,
  abstaining: XCircle,
  done: Check,
  comparing: Split,
  comparison_result: Check,
  escalation: AlertTriangle,
};

/** Steps that carry something worth showing beneath the message. */
function detail(step: ReasoningStep): string | null {
  const d = step.data;
  switch (step.stage) {
    case "classifying":
      return typeof d.query_type === "string"
        ? `${d.query_type.replace(/_/g, " ")}${d.multi_hop ? " · multi-hop" : ""}`
        : null;
    case "decomposing":
      return Array.isArray(d.sub_questions) ? d.sub_questions.join("  ·  ") : null;
    case "searching":
      return Array.isArray(d.queries)
        ? d.queries.join("  ·  ")
        : typeof d.cell_query === "string"
          ? d.cell_query
          : null;
    case "searched":
      return typeof d.count === "number" ? `${d.count} passages` : null;
    case "grading":
      return typeof d.grade === "string" ? d.grade : null;
    case "rewriting":
      return typeof d.rewritten_query === "string" ? `→ ${d.rewritten_query}` : null;
    case "conflict_found":
      return typeof d.axis === "string" ? `axis: ${d.axis}` : null;
    case "grounding_pruned":
      return typeof d.removed === "number" ? `${d.removed} unsupported sentence(s) removed` : null;
    default:
      return null;
  }
}

export function ReasoningSteps({
  steps,
  live,
  className,
}: {
  steps: ReasoningStep[];
  live: boolean;
  className?: string;
}) {
  if (steps.length === 0 && !live) return null;
  return (
    <ol
      className={cn("space-y-1.5 text-sm", className)}
      aria-label="Reasoning steps"
      aria-live={live ? "polite" : undefined}
    >
      {steps.map((step, i) => {
        const Icon = ICONS[step.stage] ?? Search;
        const isLast = i === steps.length - 1;
        const spinning = live && isLast && step.stage !== "done";
        const warn =
          step.stage === "conflict_found" || step.stage === "escalation" || step.stage === "grounding_fallback";
        const bad = step.stage === "grounding_failed" || step.stage === "abstaining";
        const extra = detail(step);
        return (
          <li key={`${step.stage}-${i}`} className="flex gap-2.5">
            <span
              className={cn(
                "mt-0.5 grid size-5 shrink-0 place-items-center rounded-full border",
                warn && "border-conflict/50 bg-conflict-bg text-conflict-fg",
                bad && "border-conf-low/40 bg-conf-low-bg text-conf-low-fg",
                !warn && !bad && "border-border bg-muted text-muted-foreground",
              )}
              aria-hidden
            >
              {spinning ? <Loader2 className="size-3 animate-spin" /> : <Icon className="size-3" />}
            </span>
            <div className="min-w-0">
              <p className={cn("leading-5", isLast && live ? "text-foreground" : "text-muted-foreground")}>
                {step.message}
              </p>
              {extra && <p className="truncate font-mono text-[11px] text-muted-foreground/80">{extra}</p>}
            </div>
          </li>
        );
      })}
      {live && steps.length === 0 && (
        <li className="flex items-center gap-2.5 text-muted-foreground">
          <Loader2 className="size-4 animate-spin" aria-hidden /> Sending…
        </li>
      )}
    </ol>
  );
}
