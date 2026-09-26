"use client";

import { useMutation } from "@tanstack/react-query";
import { Check, ClipboardCopy, FileUp, Info, Loader2, NotebookPen, RotateCcw, ShieldCheck, Sparkles } from "lucide-react";
import { useRef, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { useToken } from "@/hooks/use-api";
import { ApiError } from "@/lib/api";
import { api } from "@/lib/api-client";
import type { Schemas } from "@/lib/domain";
import { cn } from "@/lib/utils";

type Summary = Schemas["NoteSummaryOut"];

const MAX_CHARS = 15_000;

// A made-up discharge summary: every name, number and date in it is invented,
// and the ones that identify someone are exactly what the summarizer removes.
const SAMPLE = `DISCHARGE SUMMARY
Name: Priya Menon          Age/Sex: 64 Y / F
UHID: KOC-2026-118204   IP No: 4471/2026
Date of admission: 03/08/2026    Date of discharge: 09/08/2026
Consultant: Dr. Rahul Varghese (General Medicine)
Presenting complaint: Fever with chills for 4 days, burning micturition and right flank pain.
Past history: Type 2 diabetes mellitus for 12 years on metformin. Hypertension on amlodipine.
Diagnosis: Acute pyelonephritis (right) with sepsis. Uncontrolled type 2 diabetes mellitus.
Investigations:
Hb 10.8 g/dL. TLC 18,400 /cumm. Creatinine 1.6 mg/dL on admission, 1.1 mg/dL at discharge.
HbA1c 9.2%. Urine culture: E. coli, sensitive to ceftriaxone and nitrofurantoin.
USG abdomen: bulky right kidney, no hydronephrosis, no calculus.
Course in hospital: Treated with IV ceftriaxone 1 g BD for 5 days. Fever settled on day 3.
Insulin started for glycaemic control.
Discharge medications:
Tab. Cefixime 200 mg BD for 7 days
Inj. Insulin glargine 14 units SC at bedtime
Tab. Amlodipine 5 mg OD
Advice: Review in medicine OPD after 1 week with fasting blood sugar and creatinine.
Return immediately if fever recurs, vomiting prevents taking medicines, or urine output falls.
Phone: 98470 55213`;

const LABELS: Record<string, [string, string]> = {
  NAME: ["name", "names"],
  DATE: ["date", "dates"],
  "RECORD NUMBER": ["record number", "record numbers"],
  "ID NUMBER": ["ID number", "ID numbers"],
  PHONE: ["phone number", "phone numbers"],
  EMAIL: ["email", "emails"],
  ADDRESS: ["address", "addresses"],
  "PIN CODE": ["PIN code", "PIN codes"],
  LINK: ["link", "links"],
};

function removedList(redactions: Record<string, number>): string {
  return Object.entries(redactions)
    .filter(([, n]) => n > 0)
    .map(([kind, n]) => {
      const [one, many] = LABELS[kind] ?? [kind.toLowerCase(), `${kind.toLowerCase()}s`];
      return `${n} ${n === 1 ? one : many}`;
    })
    .join(" · ");
}

function asPlainText(summary: Summary): string {
  return summary.sections
    .map((section) => [section.heading, ...section.points.map((p) => `- ${p.text}`)].join("\n"))
    .join("\n\n");
}

/**
 * The clinical note summarizer: paste a note or upload a report, and get a
 * short summary under fixed headings, every point tied to the line of the
 * note it came from. Identifiers are removed on the server before the text
 * goes anywhere, and nothing is stored.
 */
export function NoteSummarizer() {
  const token = useToken();
  const [text, setText] = useState("");
  const [fileNote, setFileNote] = useState<string | null>(null);
  const [line, setLine] = useState<number | null>(null);
  const [copied, setCopied] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  const lineRefs = useRef<Record<number, HTMLLIElement | null>>({});

  const summarize = useMutation({ mutationFn: () => api.summarizeNote(token, text) });
  const readFile = useMutation({
    mutationFn: (file: File) => api.noteText(token, file),
    onSuccess: (result) => {
      setText(result.text);
      summarize.reset();
      setFileNote(
        `${result.pages ? `Read ${result.pages} page${result.pages === 1 ? "" : "s"}. ` : ""}Check the text${
          result.truncated ? " — it was cut to the first 15,000 characters —" : ""
        } then summarize.`,
      );
    },
    onError: (error) => toast.error(error instanceof ApiError ? error.message : "Couldn't read that file."),
  });

  const summary = summarize.data;
  const show = (n: number) => {
    setLine(n);
    lineRefs.current[n]?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  };

  const copy = async () => {
    if (!summary) return;
    await navigator.clipboard.writeText(asPlainText(summary));
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };

  return (
    <div className="mx-auto flex max-w-7xl flex-col gap-5 p-4 md:p-6" data-testid="note-summarizer">
      <header>
        <h1 className="flex items-center gap-2 text-xl font-semibold tracking-tight">
          <NotebookPen className="size-5 text-primary" aria-hidden /> Summarize a note
        </h1>
        <p className="text-sm text-muted-foreground">
          A long clinical note or report — discharge summary, lab or imaging report, referral — as a short summary a
          busy clinician can read at a glance. Every point shows the line it came from.
        </p>
      </header>

      <p className="flex gap-2 rounded-xl border border-guard-calm-border bg-guard-calm-bg px-3 py-2.5 text-sm text-guard-calm-fg" role="note">
        <ShieldCheck className="mt-0.5 size-4 shrink-0" aria-hidden />
        <span>
          Names, record numbers, phone numbers, addresses and exact dates are removed before anything is summarized,
          and nothing you paste is stored. Detection can miss things — only paste what you are allowed to share.
        </span>
      </p>

      {!summary && (
        <section className="flex flex-col gap-3 rounded-xl border bg-card p-4" aria-label="The note">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <Label htmlFor="note-text">The note or report</Label>
            <span className={cn("text-xs tabular-nums", text.length > MAX_CHARS ? "text-destructive" : "text-muted-foreground")}>
              {text.length.toLocaleString()} / {MAX_CHARS.toLocaleString()}
            </span>
          </div>
          <Textarea
            id="note-text"
            value={text}
            onChange={(e) => setText(e.target.value)}
            rows={14}
            placeholder="Paste the note here…"
            className="min-h-64 font-mono text-[13px] leading-5"
            data-testid="note-text"
          />
          {fileNote && (
            <p className="flex gap-1.5 text-xs text-muted-foreground" role="status">
              <Info className="mt-px size-3.5 shrink-0" aria-hidden /> {fileNote}
            </p>
          )}
          {summarize.error && (
            <p className="text-sm text-destructive" role="alert">
              {summarize.error instanceof ApiError ? summarize.error.message : "Couldn't summarize the note. Try again."}
            </p>
          )}
          <div className="flex flex-wrap gap-2">
            <Button
              onClick={() => summarize.mutate()}
              disabled={!token || text.trim().length < 20 || text.length > MAX_CHARS || summarize.isPending}
              data-testid="note-summarize"
            >
              {summarize.isPending ? <Loader2 className="animate-spin" /> : <Sparkles />} Summarize
            </Button>
            <Button variant="outline" onClick={() => fileInput.current?.click()} disabled={readFile.isPending}>
              {readFile.isPending ? <Loader2 className="animate-spin" /> : <FileUp />} Upload a report
            </Button>
            <input
              ref={fileInput}
              type="file"
              accept=".pdf,.txt,application/pdf,text/plain"
              className="hidden"
              onChange={(e) => {
                const file = e.target.files?.[0];
                if (file) readFile.mutate(file);
                e.target.value = "";
              }}
              data-testid="note-file"
            />
            <Button
              variant="ghost"
              onClick={() => {
                setText(SAMPLE);
                setFileNote("A made-up discharge summary — every name, number and date in it is invented.");
              }}
              data-testid="note-sample"
            >
              Try a sample note
            </Button>
          </div>
        </section>
      )}

      {summary && (
        <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_minmax(0,26rem)]">
          <section className="flex flex-col gap-3" aria-labelledby="summary-heading" data-testid="note-summary">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <h2 id="summary-heading" className="text-lg font-semibold tracking-tight">
                Summary
              </h2>
              <div className="flex gap-2">
                <Button size="sm" variant="outline" onClick={() => void copy()}>
                  {copied ? <Check /> : <ClipboardCopy />} {copied ? "Copied" : "Copy"}
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => {
                    summarize.reset();
                    setLine(null);
                  }}
                  data-testid="note-again"
                >
                  <RotateCcw /> Another note
                </Button>
              </div>
            </div>
            <p className="flex flex-wrap gap-1.5 text-xs text-muted-foreground" data-testid="note-redactions">
              <ShieldCheck className="size-3.5" aria-hidden />
              {removedList(summary.redactions)
                ? `Removed before summarizing: ${removedList(summary.redactions)}.`
                : "No identifiers were found."}
            </p>
            {summary.notice && (
              <p className="flex gap-2 rounded-lg border bg-muted/40 px-3 py-2 text-xs text-muted-foreground" role="note">
                <Info className="mt-px size-3.5 shrink-0" aria-hidden /> {summary.notice}
              </p>
            )}
            {summary.sections.length === 0 && (
              <p className="rounded-xl border bg-card p-4 text-sm text-muted-foreground">
                Nothing in this text looks like a clinical note or report to summarize.
              </p>
            )}
            {summary.sections.map((section) => (
              <div key={section.heading} className="rounded-xl border bg-card p-4">
                <h3 className="mb-2 text-sm font-semibold">{section.heading}</h3>
                <ul className="flex flex-col gap-1.5 text-sm leading-6">
                  {section.points.map((point) => (
                    <li key={`${point.text}-${point.lines.join(",")}`} className="flex gap-2">
                      <span className="mt-2.5 size-1.5 shrink-0 rounded-full bg-muted-foreground/60" aria-hidden />
                      <span className="min-w-0">
                        {point.text}
                        {point.lines.map((n) => (
                          <button
                            key={n}
                            type="button"
                            onClick={() => show(n)}
                            className={cn(
                              "ml-1 inline-flex h-4 min-w-4 items-center justify-center rounded bg-citation-bg px-1 align-text-top text-[10px] font-semibold text-citation-fg hover:ring-1 hover:ring-citation",
                              line === n && "ring-1 ring-citation",
                            )}
                            aria-label={`Line ${n} of the note`}
                          >
                            {n}
                          </button>
                        ))}
                        {point.support === "partially_supported" && (
                          <span className="ml-1.5 text-[11px] text-muted-foreground">(partly matched)</span>
                        )}
                      </span>
                    </li>
                  ))}
                </ul>
              </div>
            ))}
            {summary.removed > 0 && (
              <p className="text-xs text-muted-foreground">
                {summary.removed} point{summary.removed === 1 ? " was" : "s were"} left out because{" "}
                {summary.removed === 1 ? "it" : "they"} didn’t match the note.
              </p>
            )}
          </section>

          <aside className="flex flex-col gap-2 lg:sticky lg:top-4 lg:self-start" aria-labelledby="read-heading">
            <h2 id="read-heading" className="text-sm font-semibold">
              The note as it was read
            </h2>
            <p className="text-xs text-muted-foreground">Identifiers already replaced. Tap a number in the summary to find its line.</p>
            <ol className="max-h-[70vh] overflow-y-auto rounded-xl border bg-card p-2 font-mono text-[12px] leading-5" data-testid="note-lines">
              {summary.lines.map((text, i) => (
                <li
                  key={i}
                  ref={(el) => {
                    lineRefs.current[i + 1] = el;
                  }}
                  className={cn("flex gap-2 rounded px-1.5 py-0.5", line === i + 1 && "bg-highlight/40")}
                >
                  <span className="w-6 shrink-0 select-none text-right text-muted-foreground">{i + 1}</span>
                  <span className="min-w-0 break-words">{text}</span>
                </li>
              ))}
            </ol>
          </aside>
        </div>
      )}
    </div>
  );
}
