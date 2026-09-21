"use client";

import { ThumbsDown, ThumbsUp } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Textarea } from "@/components/ui/textarea";
import { useFeedback } from "@/hooks/use-api";
import type { FeedbackReason } from "@/lib/domain";
import { cn } from "@/lib/utils";

const REASONS: Array<{ value: NonNullable<FeedbackReason>; label: string; hint: string }> = [
  { value: "wrong", label: "Wrong", hint: "The answer states something incorrect." },
  { value: "unsupported", label: "Unsupported", hint: "The cited passages don't support the claim." },
  { value: "outdated", label: "Outdated", hint: "Newer evidence changes the answer." },
  { value: "incomplete", label: "Incomplete", hint: "It misses something a clinician would need." },
  {
    value: "should_have_abstained",
    label: "Should have abstained",
    hint: "The evidence wasn't strong enough to answer at all.",
  },
];

/**
 * Thumbs with the structured reason picker (spec #12). Optimistic: the thumb
 * lights up instantly; if the request fails it rolls back with a toast.
 */
export function FeedbackBar({ answerId, className }: { answerId: string; className?: string }) {
  const feedback = useFeedback();
  const [rating, setRating] = useState<"up" | "down" | null>(null);
  const [reason, setReason] = useState<NonNullable<FeedbackReason> | null>(null);
  const [comment, setComment] = useState("");
  const [open, setOpen] = useState(false);

  const submit = (next: "up" | "down", nextReason: NonNullable<FeedbackReason> | null, note: string) => {
    const previous = { rating, reason };
    setRating(next);
    setReason(nextReason);
    feedback.mutate(
      { answer_id: answerId, rating: next, reason: next === "down" ? nextReason : null, comment: note || null },
      {
        onSuccess: () => {
          setOpen(false);
          toast.success(next === "up" ? "Thanks — marked as helpful." : "Thanks — feedback recorded.");
        },
        onError: (error) => {
          setRating(previous.rating);
          setReason(previous.reason);
          toast.error(error instanceof Error ? error.message : "Could not save feedback.");
        },
      },
    );
  };

  return (
    <div className={cn("flex items-center gap-1", className)} role="group" aria-label="Was this answer helpful?">
      <Button
        variant="ghost"
        size="icon-sm"
        aria-pressed={rating === "up"}
        aria-label="Helpful"
        onClick={() => submit("up", null, "")}
        className={cn(rating === "up" && "bg-conf-high-bg text-conf-high-fg")}
      >
        <ThumbsUp className="size-4" />
      </Button>
      <Popover open={open} onOpenChange={setOpen}>
        <PopoverTrigger
          render={
            <Button
              variant="ghost"
              size="icon-sm"
              aria-pressed={rating === "down"}
              aria-label="Not helpful — choose a reason"
              className={cn(rating === "down" && "bg-conf-low-bg text-conf-low-fg")}
            />
          }
        >
          <ThumbsDown className="size-4" />
        </PopoverTrigger>
        <PopoverContent align="start" className="w-80 p-3">
          <p className="mb-2 text-sm font-medium">What went wrong?</p>
          <div className="flex flex-col gap-1" role="radiogroup" aria-label="Reason">
            {REASONS.map((r) => (
              <button
                key={r.value}
                type="button"
                role="radio"
                aria-checked={reason === r.value}
                onClick={() => setReason(r.value)}
                className={cn(
                  "flex flex-col items-start rounded-sm px-2 py-1.5 text-left text-sm hover:bg-accent focus-visible:bg-accent",
                  reason === r.value && "bg-accent",
                )}
              >
                <span className="font-medium">{r.label}</span>
                <span className="text-xs text-muted-foreground">{r.hint}</span>
              </button>
            ))}
          </div>
          <Textarea
            value={comment}
            onChange={(e) => setComment(e.target.value)}
            placeholder="Optional: what would a better answer have said?"
            rows={2}
            className="mt-2 text-sm"
            aria-label="Comment"
          />
          <div className="mt-2 flex justify-end gap-2">
            <Button variant="ghost" size="sm" onClick={() => setOpen(false)}>
              Cancel
            </Button>
            <Button size="sm" disabled={!reason || feedback.isPending} onClick={() => submit("down", reason, comment)}>
              Send
            </Button>
          </div>
        </PopoverContent>
      </Popover>
      {rating === "down" && reason && (
        <span className="ml-1 text-xs text-muted-foreground">
          {REASONS.find((r) => r.value === reason)?.label}
        </span>
      )}
    </div>
  );
}
