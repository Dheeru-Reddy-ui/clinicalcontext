import type { ComponentProps } from "react";

import type { Confidence, EvidenceGrade, Stance } from "@/lib/domain";
import { cn } from "@/lib/utils";

/**
 * The semantic vocabulary of the product, rendered one way everywhere.
 * Colour reinforces; the label carries the meaning (WCAG 1.4.1).
 */

const CONFIDENCE: Record<Confidence, { label: string; className: string; dot: string }> = {
  high: {
    label: "High confidence",
    className: "bg-conf-high-bg text-conf-high-fg border-conf-high/30",
    dot: "bg-conf-high",
  },
  moderate: {
    label: "Moderate confidence",
    className: "bg-conf-moderate-bg text-conf-moderate-fg border-conf-moderate/30",
    dot: "bg-conf-moderate",
  },
  low: {
    label: "Low confidence",
    className: "bg-conf-low-bg text-conf-low-fg border-conf-low/30",
    dot: "bg-conf-low",
  },
};

const GRADE: Record<EvidenceGrade | "none", { label: string; className: string }> = {
  A: { label: "Grade A", className: "bg-grade-a-bg text-grade-a-fg border-grade-a/35" },
  B: { label: "Grade B", className: "bg-grade-b-bg text-grade-b-fg border-grade-b/35" },
  C: { label: "Grade C", className: "bg-grade-c-bg text-grade-c-fg border-grade-c/35" },
  D: { label: "Grade D", className: "bg-grade-d-bg text-grade-d-fg border-grade-d/35" },
  none: { label: "Ungraded", className: "bg-grade-none-bg text-grade-none-fg border-grade-none/35" },
};

export const GRADE_DESCRIPTION: Record<EvidenceGrade | "none", string> = {
  A: "Systematic reviews, meta-analyses, or well-designed randomized trials.",
  B: "Individual RCTs or high-quality cohort studies.",
  C: "Case-control, case series, or lower-quality observational studies.",
  D: "Expert opinion, case reports, or mechanistic reasoning.",
  none: "The source has not been graded.",
};

const base =
  "inline-flex items-center gap-1.5 rounded-sm border px-1.5 py-0.5 text-xs font-medium leading-none whitespace-nowrap";

export function ConfidenceBadge({
  level,
  className,
  compact = false,
  ...rest
}: { level: Confidence; compact?: boolean } & ComponentProps<"span">) {
  const c = CONFIDENCE[level];
  return (
    <span
      className={cn(base, c.className, className)}
      aria-label={c.label}
      title={c.label}
      data-confidence={level}
      {...rest}
    >
      <span aria-hidden className={cn("size-1.5 rounded-full", c.dot)} />
      {compact ? level : c.label}
    </span>
  );
}

export function GradeBadge({
  grade,
  className,
  showWord = false,
  ...rest
}: { grade: EvidenceGrade | null | undefined; showWord?: boolean } & ComponentProps<"span">) {
  const key = grade ?? "none";
  const g = GRADE[key];
  return (
    <span
      className={cn(base, "font-mono", g.className, className)}
      aria-label={`Evidence ${g.label.toLowerCase()}`}
      title={`${g.label} — ${GRADE_DESCRIPTION[key]}`}
      data-grade={key}
      {...rest}
    >
      {showWord ? g.label : grade ?? "—"}
    </span>
  );
}

const STANCE: Record<Stance, { label: string; className: string }> = {
  supports: { label: "Supports", className: "bg-stance-supports" },
  opposes: { label: "Opposes", className: "bg-stance-opposes" },
  neutral: { label: "Neutral", className: "bg-stance-neutral" },
};

export function StanceDot({ stance, className }: { stance: Stance; className?: string }) {
  const s = STANCE[stance];
  return (
    <span className="inline-flex items-center gap-1.5 text-xs text-muted-foreground">
      <span aria-hidden className={cn("size-2 rounded-full", s.className, className)} />
      {s.label}
    </span>
  );
}

export function StatusPill({
  kind,
  className,
  ...rest
}: {
  kind: "cached" | "abstained" | "conflict" | "blocked" | "escalation" | "superseded";
} & ComponentProps<"span">) {
  const map = {
    cached: { label: "Cached", className: "bg-cached-bg text-cached-fg border-transparent" },
    abstained: { label: "Abstained", className: "bg-conf-low-bg text-conf-low-fg border-conf-low/30" },
    conflict: {
      label: "Sources disagree",
      className: "bg-conflict-bg text-conflict-fg border-conflict/40",
    },
    blocked: { label: "Blocked", className: "bg-guard-calm-bg text-guard-calm-fg border-guard-calm-border" },
    escalation: { label: "Red flag", className: "bg-redflag-bg text-redflag-fg border-transparent" },
    superseded: {
      label: "Superseded",
      className: "bg-conflict-bg text-conflict-fg border-conflict/40",
    },
  } as const;
  const s = map[kind];
  return (
    <span className={cn(base, s.className, className)} data-status={kind} {...rest}>
      {s.label}
    </span>
  );
}
