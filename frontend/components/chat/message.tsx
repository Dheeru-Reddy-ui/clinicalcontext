"use client";

import {
  AlertTriangle,
  BookOpenCheck,
  Check,
  ChevronDown,
  Copy,
  FlaskConical,
  Loader2,
  ShieldAlert,
  Sparkles,
  Stethoscope,
} from "lucide-react";
import Link from "next/link";
import { useMemo, useRef, useState } from "react";

import { EvidenceTimeline } from "@/components/answer/evidence-timeline";
import { Markdown } from "@/components/chat/markdown";
import { SourceList } from "@/components/chat/sources";
import { Button } from "@/components/ui/button";
import type { AssistantMessage, UserMessage } from "@/hooks/use-chat";
import { PROGRESS_LABELS, type ChatResult } from "@/lib/chat";
import { EMPTY_CONTRADICTION } from "@/lib/domain";
import { cn } from "@/lib/utils";

const COMPLAINT_NAMES: Record<string, string> = {
  fever: "Fever",
  cough_cold: "Cough, cold or sore throat",
  diarrhoea_vomiting: "Diarrhoea or vomiting",
  headache: "Headache",
  urinary: "Burning or pain when passing urine",
  rash_allergy: "Rash, hives or allergy",
  body_pain: "Body, joint or back pain",
};

export function UserBubble({ message }: { message: UserMessage }) {
  return (
    <div className="flex justify-end" data-testid="chat-user">
      <div className="max-w-[85%] whitespace-pre-wrap rounded-2xl rounded-br-md bg-muted px-4 py-2.5 text-[15px] leading-6">
        {message.text}
      </div>
    </div>
  );
}

function Progress({ steps }: { steps: AssistantMessage["steps"] }) {
  const visible = steps.filter((s) => PROGRESS_LABELS[s.key]);
  if (!visible.length) {
    return (
      <p className="flex items-center gap-2 text-sm text-muted-foreground">
        <Loader2 className="size-3.5 animate-spin" aria-hidden /> Thinking…
      </p>
    );
  }
  return (
    <ol className="flex flex-col gap-1 text-sm text-muted-foreground" aria-label="Progress">
      {visible.map((s, i) => {
        const last = i === visible.length - 1;
        return (
          <li key={`${s.key}${i}`} className="flex items-center gap-2">
            {last ? (
              <Loader2 className="size-3.5 animate-spin" aria-hidden />
            ) : (
              <Check className="size-3.5 text-emerald-600" aria-hidden />
            )}
            {s.key === "live_found" ? s.message : (PROGRESS_LABELS[s.key] ?? s.message)}
          </li>
        );
      })}
    </ol>
  );
}

function CheckBadge({ result }: { result: ChatResult }) {
  const check = result.check;
  if (!check) return null;
  const cited = check.backed + check.partly + check.unmatched;
  if (cited === 0 && check.general === 0) return null;
  const allBacked = check.unmatched === 0 && cited > 0;
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs",
        allBacked
          ? "border-emerald-600/30 bg-emerald-50 text-emerald-800 dark:bg-emerald-950/40 dark:text-emerald-300"
          : "border-amber-600/30 bg-amber-50 text-amber-900 dark:bg-amber-950/40 dark:text-amber-200",
      )}
      title="Every statement with a source marker was compared with the passage it cites."
      data-testid="chat-check"
    >
      <BookOpenCheck className="size-3" aria-hidden />
      {cited > 0
        ? `${check.backed + check.partly} of ${cited} cited statements match their sources`
        : "No specific source cited"}
      {check.general > 0 && ` · ${check.general} general`}
    </span>
  );
}

function ProviderBadge({ result }: { result: ChatResult }) {
  const label =
    result.mode === "llm" || result.mode === "conversation"
      ? result.provider === "local"
        ? "Assistant"
        : `Written by ${result.model.replace(/^openai\//, "")} · ${result.provider}`
      : "Quoted from sources";
  return (
    <span className="inline-flex items-center gap-1 text-xs text-muted-foreground" data-testid="chat-provider">
      <Sparkles className="size-3" aria-hidden /> {label}
    </span>
  );
}

export function AssistantBubble({
  message,
  onRetry,
  showTimeline = true,
  checkHref = "/app/treatment",
}: {
  message: AssistantMessage;
  onRetry?: () => void;
  showTimeline?: boolean;
  checkHref?: string;
}) {
  const [active, setActive] = useState<number | null>(null);
  const [showSources, setShowSources] = useState(false);
  const [copied, setCopied] = useState(false);
  const sourcesRef = useRef<HTMLOListElement>(null);
  const result = message.result;
  const citations = useMemo(() => result?.citations ?? [], [result]);
  const known = useMemo(() => new Set(citations.map((c) => c.marker)), [citations]);
  const streaming = message.status === "streaming";

  const cite = (marker: number) => {
    setActive(marker);
    setShowSources(true);
    requestAnimationFrame(() =>
      sourcesRef.current
        ?.querySelector(`[data-marker="${marker}"]`)
        ?.scrollIntoView({ behavior: "smooth", block: "nearest" }),
    );
  };

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(message.text);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      /* clipboard blocked: nothing to do */
    }
  };

  return (
    <div className="flex gap-3" data-testid="chat-assistant" data-status={message.status}>
      <span
        aria-hidden
        className="mt-0.5 grid size-7 shrink-0 place-items-center rounded-full bg-primary font-mono text-[10px] font-bold text-primary-foreground"
      >
        CC
      </span>
      <div className="min-w-0 flex-1 space-y-3">
        {message.escalation && (
          <div
            role="alert"
            className="flex gap-2 rounded-md bg-redflag px-3 py-2.5 text-sm font-medium text-redflag-fg"
            data-testid="chat-escalation"
          >
            <ShieldAlert className="mt-0.5 size-4 shrink-0" aria-hidden />
            <span>{message.escalation}</span>
          </div>
        )}

        {message.blocked && (
          <div
            role="alert"
            className="flex gap-2 rounded-md border border-amber-600/40 bg-amber-50 px-3 py-2.5 text-sm text-amber-950 dark:bg-amber-950/40 dark:text-amber-100"
            data-testid="chat-blocked"
          >
            <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden />
            <span>{message.blocked.message}</span>
          </div>
        )}

        {streaming && !message.text && <Progress steps={message.steps} />}

        {message.text && (
          <Markdown
            text={message.text}
            known={known}
            activeMarker={active}
            onCite={citations.length ? cite : undefined}
            streaming={streaming}
          />
        )}

        {message.status === "error" && (
          <div
            role="alert"
            className="flex flex-wrap items-center gap-2 rounded-md border border-destructive/40 px-3 py-2 text-sm text-destructive"
            data-testid="chat-error"
          >
            <AlertTriangle className="size-4" aria-hidden />
            <span className="flex-1">{message.error}</span>
            {onRetry && (
              <Button size="sm" variant="outline" onClick={onRetry}>
                Try again
              </Button>
            )}
          </div>
        )}

        {result?.suggestCheck && (
          <Link
            href={`${checkHref}?complaint=${result.suggestCheck}`}
            className="flex items-center gap-3 rounded-lg border border-primary/30 bg-primary/5 p-3 text-sm transition-colors hover:bg-primary/10"
            data-testid="chat-suggest-check"
          >
            <Stethoscope className="size-5 shrink-0 text-primary" aria-hidden />
            <span className="flex-1">
              <span className="font-medium">
                Get personal advice: {COMPLAINT_NAMES[result.suggestCheck] ?? "Symptom"} check
              </span>
              <span className="block text-muted-foreground">
                A few questions about warning signs, then what to do and safe medicine doses for the
                person’s age.
              </span>
            </span>
          </Link>
        )}

        {result && message.status === "done" && (
          <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
            <ProviderBadge result={result} />
            <CheckBadge result={result} />
            {result.live?.searched && (
              <span className="inline-flex items-center gap-1 text-xs text-muted-foreground">
                <FlaskConical className="size-3" aria-hidden />
                PubMed searched{result.live.found ? ` · ${result.live.found} papers` : ""}
              </span>
            )}
            <span className="ml-auto flex items-center gap-1">
              {citations.length > 0 && (
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => setShowSources((v) => !v)}
                  aria-expanded={showSources}
                  data-testid="chat-sources-toggle"
                >
                  Sources ({citations.length})
                  <ChevronDown className={cn("transition-transform", showSources && "rotate-180")} aria-hidden />
                </Button>
              )}
              <Button variant="ghost" size="icon-sm" onClick={copy} aria-label="Copy answer">
                {copied ? <Check /> : <Copy />}
              </Button>
            </span>
          </div>
        )}

        {showSources && citations.length > 0 && (
          <SourceList ref={sourcesRef} citations={citations} activeMarker={active} />
        )}

        {showTimeline && showSources && citations.length >= 2 && (
          <EvidenceTimeline
            citations={citations}
            contradiction={EMPTY_CONTRADICTION}
            activeMarker={active}
            onCite={cite}
          />
        )}
      </div>
    </div>
  );
}
