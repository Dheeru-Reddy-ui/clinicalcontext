"use client";

import { ArrowUp, Square } from "lucide-react";
import { useEffect, useRef, useState, type KeyboardEvent, type ReactNode } from "react";

import { Button } from "@/components/ui/button";
import { AUDIENCE_LABELS, type Audience } from "@/lib/chat";
import { cn } from "@/lib/utils";

export function AudienceSwitch({
  value,
  onChange,
  disabled,
  className,
}: {
  value: Audience;
  onChange: (audience: Audience) => void;
  disabled?: boolean;
  className?: string;
}) {
  return (
    <div
      role="radiogroup"
      aria-label="Answer for"
      className={cn("inline-flex rounded-md border bg-background p-0.5", className)}
    >
      {(Object.keys(AUDIENCE_LABELS) as Audience[]).map((key) => (
        <button
          key={key}
          type="button"
          role="radio"
          aria-checked={value === key}
          disabled={disabled}
          title={AUDIENCE_LABELS[key].hint}
          onClick={() => onChange(key)}
          data-testid={`audience-${key}`}
          className={cn(
            "rounded-[5px] px-2.5 py-1 text-xs font-medium transition-colors disabled:opacity-50",
            value === key ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:text-foreground",
          )}
        >
          {AUDIENCE_LABELS[key].label}
        </button>
      ))}
    </div>
  );
}

/**
 * The message box: grows with the text, Enter sends and Shift+Enter starts a
 * new line, and while an answer streams the send button becomes Stop.
 */
export function Composer({
  onSend,
  onStop,
  busy,
  placeholder = "Ask anything about health or medicine…",
  autoFocus,
  leading,
  trailing,
  footer,
  initial = "",
}: {
  onSend: (text: string) => void;
  onStop: () => void;
  busy: boolean;
  placeholder?: string;
  autoFocus?: boolean;
  leading?: ReactNode;
  /** Extra controls; a function receives `append` to add text (voice typing). */
  trailing?: ReactNode | ((append: (text: string) => void) => ReactNode);
  footer?: ReactNode;
  initial?: string;
}) {
  const [text, setText] = useState(initial);
  const area = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    const el = area.current;
    if (!el) return;
    el.style.height = "0px";
    el.style.height = `${Math.min(el.scrollHeight, 220)}px`;
  }, [text]);

  useEffect(() => setText(initial), [initial]);

  const append = (addition: string) => {
    setText((current) => (current.trim() ? `${current.trimEnd()} ${addition}` : addition));
    area.current?.focus();
  };

  const submit = () => {
    const value = text.trim();
    if (!value || busy) return;
    onSend(value);
    setText("");
  };

  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      submit();
    }
  };

  return (
    <div className="rounded-2xl border bg-card shadow-sm focus-within:ring-2 focus-within:ring-ring/40">
      <textarea
        ref={area}
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={onKeyDown}
        placeholder={placeholder}
        rows={1}
        autoFocus={autoFocus}
        maxLength={4000}
        aria-label="Message"
        data-testid="chat-input"
        className="block max-h-56 w-full resize-none bg-transparent px-4 pt-3 pb-1 text-[15px] leading-6 outline-none placeholder:text-muted-foreground"
      />
      <div className="flex items-center gap-2 px-2 pb-2">
        {leading}
        <div className="ml-auto flex items-center gap-1.5">
          {typeof trailing === "function" ? trailing(append) : trailing}
          {busy ? (
            <Button size="icon" variant="secondary" onClick={onStop} aria-label="Stop answering" data-testid="chat-stop">
              <Square className="size-3.5 fill-current" />
            </Button>
          ) : (
            <Button
              size="icon"
              onClick={submit}
              disabled={!text.trim()}
              aria-label="Send message"
              data-testid="chat-send"
              className="rounded-full"
            >
              <ArrowUp />
            </Button>
          )}
        </div>
      </div>
      {footer && <div className="border-t px-3 py-1.5 text-[11px] text-muted-foreground">{footer}</div>}
    </div>
  );
}
