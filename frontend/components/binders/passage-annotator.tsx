"use client";

import { Highlighter, MessageSquareReply } from "lucide-react";
import { useCallback, useMemo, useRef, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { useAnnotate } from "@/hooks/use-api";
import type { Schemas } from "@/lib/domain";
import { formatDate } from "@/lib/text";
import { cn } from "@/lib/utils";

type Annotation = Schemas["AnnotationOut"];

interface Range {
  start: number;
  end: number;
}

/** Character offsets of the current selection inside `container`'s text. */
function selectionOffsets(container: HTMLElement): Range | null {
  const sel = window.getSelection();
  if (!sel || sel.rangeCount === 0 || sel.isCollapsed) return null;
  const range = sel.getRangeAt(0);
  if (!container.contains(range.commonAncestorContainer)) return null;
  const pre = document.createRange();
  pre.selectNodeContents(container);
  pre.setEnd(range.startContainer, range.startOffset);
  const start = pre.toString().length;
  const end = start + range.toString().length;
  return end > start ? { start, end } : null;
}

/** Split text into segments so overlapping highlights render once each. */
function segments(text: string, ranges: Array<Range & { id: string }>) {
  const cuts = new Set<number>([0, text.length]);
  for (const r of ranges) {
    cuts.add(Math.max(0, Math.min(r.start, text.length)));
    cuts.add(Math.max(0, Math.min(r.end, text.length)));
  }
  const points = [...cuts].sort((a, b) => a - b);
  const out: Array<{ start: number; end: number; ids: string[] }> = [];
  for (let i = 0; i < points.length - 1; i += 1) {
    const s = points[i] ?? 0;
    const e = points[i + 1] ?? 0;
    if (e <= s) continue;
    out.push({ start: s, end: e, ids: ranges.filter((r) => r.start <= s && r.end >= e).map((r) => r.id) });
  }
  return out;
}

/**
 * Inline highlight-and-annotate on a source passage (spec #18): select text,
 * annotate; each annotation shows its author, and replies thread beneath it.
 */
export function PassageAnnotator({
  binderId,
  itemId,
  text,
  annotations,
  readOnly = false,
  present = false,
}: {
  binderId: string;
  itemId: string;
  text: string;
  annotations: Annotation[];
  readOnly?: boolean;
  present?: boolean;
}) {
  const ref = useRef<HTMLParagraphElement>(null);
  const [pending, setPending] = useState<Range | null>(null);
  const [draft, setDraft] = useState("");
  const [focused, setFocused] = useState<string | null>(null);
  const annotate = useAnnotate(binderId);

  const ranges = useMemo(
    () =>
      annotations
        .filter((a) => a.highlight_range && typeof a.highlight_range.start === "number" && typeof a.highlight_range.end === "number")
        .map((a) => ({ id: a.id, start: a.highlight_range?.start ?? 0, end: a.highlight_range?.end ?? 0 })),
    [annotations],
  );
  const parts = useMemo(() => segments(text, ranges), [text, ranges]);

  const onMouseUp = useCallback(() => {
    if (readOnly || present || !ref.current) return;
    setPending(selectionOffsets(ref.current));
  }, [readOnly, present]);

  const submit = (body: string, range: Range | null, parentId: string | null) =>
    annotate.mutate(
      {
        itemId,
        body: { body, highlight_range: range ? { start: range.start, end: range.end } : null, parent_id: parentId },
      },
      {
        onSuccess: () => {
          setPending(null);
          setDraft("");
          toast.success(parentId ? "Reply posted." : "Annotation saved.");
        },
        onError: (e) => toast.error(e instanceof Error ? e.message : "Could not save."),
      },
    );

  return (
    <div className="space-y-3">
      <p
        ref={ref}
        onMouseUp={onMouseUp}
        className={cn("leading-7", present ? "text-lg" : "text-sm")}
        data-testid="annotatable-passage"
      >
        {parts.map((seg) =>
          seg.ids.length > 0 ? (
            <mark
              key={`${seg.start}-${seg.end}`}
              className={cn(
                "passage-highlight cursor-pointer",
                seg.ids.some((i) => i === focused) && "ring-2 ring-ring",
              )}
              onClick={() => setFocused(seg.ids[0] ?? null)}
              title={`${seg.ids.length} annotation${seg.ids.length === 1 ? "" : "s"}`}
            >
              {text.slice(seg.start, seg.end)}
            </mark>
          ) : (
            <span key={`${seg.start}-${seg.end}`}>{text.slice(seg.start, seg.end)}</span>
          ),
        )}
      </p>

      {pending && !readOnly && !present && (
        <div className="rounded-md border bg-muted/40 p-2" role="dialog" aria-label="Annotate selection">
          <p className="mb-1 text-xs text-muted-foreground">
            <Highlighter className="mr-1 inline size-3" aria-hidden />
            Annotating: “{text.slice(pending.start, pending.end).slice(0, 80)}{pending.end - pending.start > 80 ? "…" : ""}”
          </p>
          <Textarea value={draft} onChange={(e) => setDraft(e.target.value)} rows={2} placeholder="Your note for the group…" autoFocus className="text-sm" />
          <div className="mt-2 flex justify-end gap-2">
            <Button size="xs" variant="ghost" onClick={() => setPending(null)}>Cancel</Button>
            <Button size="xs" disabled={!draft.trim() || annotate.isPending} onClick={() => submit(draft.trim(), pending, null)}>Save</Button>
          </div>
        </div>
      )}

      {!pending && !readOnly && !present && annotations.length === 0 && (
        <p className="text-xs text-muted-foreground">Select any text above to annotate it.</p>
      )}

      {annotations.length > 0 && (
        <ol className="space-y-2" aria-label="Annotations">
          {annotations.map((a) => (
            <Thread key={a.id} annotation={a} focused={focused === a.id} readOnly={readOnly || present} onReply={(body) => submit(body, null, a.id)} busy={annotate.isPending} />
          ))}
        </ol>
      )}
    </div>
  );
}

function Thread({
  annotation,
  focused,
  readOnly,
  onReply,
  busy,
}: {
  annotation: Annotation;
  focused: boolean;
  readOnly: boolean;
  onReply: (body: string) => void;
  busy: boolean;
}) {
  const [replying, setReplying] = useState(false);
  const [draft, setDraft] = useState("");
  return (
    <li className={cn("rounded-md border p-3 text-sm", focused && "ring-2 ring-ring")}>
      <Note a={annotation} />
      {(annotation.replies ?? []).length > 0 && (
        <ol className="mt-2 space-y-2 border-l-2 pl-3">
          {(annotation.replies ?? []).map((r) => (
            <li key={r.id}>
              <Note a={r} />
            </li>
          ))}
        </ol>
      )}
      {!readOnly && (
        <div className="mt-2">
          {replying ? (
            <div>
              <Textarea value={draft} onChange={(e) => setDraft(e.target.value)} rows={2} placeholder="Reply…" autoFocus className="text-sm" />
              <div className="mt-1.5 flex justify-end gap-2">
                <Button size="xs" variant="ghost" onClick={() => setReplying(false)}>Cancel</Button>
                <Button size="xs" disabled={!draft.trim() || busy} onClick={() => { onReply(draft.trim()); setDraft(""); setReplying(false); }}>Reply</Button>
              </div>
            </div>
          ) : (
            <Button size="xs" variant="ghost" onClick={() => setReplying(true)}>
              <MessageSquareReply /> Reply
            </Button>
          )}
        </div>
      )}
    </li>
  );
}

function Note({ a }: { a: Annotation }) {
  return (
    <div>
      <p className="text-[11px] text-muted-foreground">
        <span className="font-medium text-foreground">{a.author_name ?? "Colleague"}</span> ·{" "}
        <time dateTime={a.created_at}>{formatDate(a.created_at, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })}</time>
      </p>
      <p className="mt-0.5 whitespace-pre-wrap leading-6">{a.body}</p>
    </div>
  );
}
