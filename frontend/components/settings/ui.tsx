"use client";

import { Check } from "lucide-react";
import { useEffect, useRef, useState, type KeyboardEvent, type ReactNode } from "react";

import { cn } from "@/lib/utils";

/**
 * The pieces every Settings section is built from: a titled card, a row
 * with its explanation on the left and its control on the right (stacked on
 * a phone), and a segmented choice that behaves like a radio group.
 */

export function SettingsCard({
  title,
  description,
  children,
  aside,
  className,
  testId,
}: {
  title: string;
  description?: ReactNode;
  children: ReactNode;
  aside?: ReactNode;
  className?: string;
  testId?: string;
}) {
  return (
    <section className={cn("rounded-2xl border bg-card p-4 sm:p-5", className)} data-testid={testId}>
      <header className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <h2 className="text-[15px] font-semibold tracking-tight">{title}</h2>
          {description && <p className="mt-1 text-sm text-pretty text-muted-foreground">{description}</p>}
        </div>
        {aside}
      </header>
      <div className="mt-4 flex flex-col divide-y">{children}</div>
    </section>
  );
}

export function SettingRow({
  label,
  description,
  control,
  htmlFor,
  children,
}: {
  label: string;
  description?: ReactNode;
  control?: ReactNode;
  htmlFor?: string;
  children?: ReactNode;
}) {
  return (
    <div className="flex flex-col gap-3 py-3.5 first:pt-0 last:pb-0 sm:flex-row sm:items-center sm:justify-between">
      <div className="min-w-0 sm:max-w-[60%]">
        {htmlFor ? (
          <label htmlFor={htmlFor} className="text-sm font-medium">
            {label}
          </label>
        ) : (
          <p className="text-sm font-medium">{label}</p>
        )}
        {description && <p className="mt-0.5 text-xs text-pretty text-muted-foreground">{description}</p>}
        {children}
      </div>
      {control && <div className="shrink-0">{control}</div>}
    </div>
  );
}

export interface Choice<T extends string> {
  value: T;
  label: string;
  hint?: string;
}

/** A single choice among a few, as a row of buttons; arrow keys move between them. */
export function Segmented<T extends string>({
  label,
  value,
  choices,
  onChange,
  disabled,
  testId,
  className,
}: {
  label: string;
  value: T;
  choices: ReadonlyArray<Choice<T>>;
  onChange: (value: T) => void;
  disabled?: boolean;
  testId?: string;
  className?: string;
}) {
  const refs = useRef<Array<HTMLButtonElement | null>>([]);
  const index = Math.max(0, choices.findIndex((c) => c.value === value));

  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    const step = event.key === "ArrowRight" || event.key === "ArrowDown" ? 1 : event.key === "ArrowLeft" || event.key === "ArrowUp" ? -1 : 0;
    if (!step || disabled) return;
    event.preventDefault();
    const next = (index + step + choices.length) % choices.length;
    const choice = choices[next];
    if (!choice) return;
    onChange(choice.value);
    refs.current[next]?.focus();
  };

  return (
    <div
      role="radiogroup"
      aria-label={label}
      onKeyDown={onKeyDown}
      data-testid={testId}
      className={cn("inline-flex max-w-full flex-wrap gap-0.5 rounded-lg border bg-muted/40 p-0.5", className)}
    >
      {choices.map((choice, i) => {
        const selected = choice.value === value;
        return (
          <button
            key={choice.value}
            ref={(el) => {
              refs.current[i] = el;
            }}
            type="button"
            role="radio"
            aria-checked={selected}
            tabIndex={selected ? 0 : -1}
            disabled={disabled}
            title={choice.hint}
            onClick={() => onChange(choice.value)}
            data-testid={testId ? `${testId}-${choice.value}` : undefined}
            className={cn(
              "min-h-8 rounded-md px-3 text-sm font-medium transition-colors disabled:opacity-50",
              selected
                ? "bg-background text-foreground shadow-sm ring-1 ring-border"
                : "text-muted-foreground hover:text-foreground",
            )}
          >
            {choice.label}
          </button>
        );
      })}
    </div>
  );
}

/** "Saved" for a moment after a change lands — quieter than a toast per toggle. */
export function SavedHint({ savedAt }: { savedAt: number }) {
  const [visible, setVisible] = useState(false);
  useEffect(() => {
    if (!savedAt) return;
    setVisible(true);
    const timer = setTimeout(() => setVisible(false), 2000);
    return () => clearTimeout(timer);
  }, [savedAt]);
  return (
    <span
      role="status"
      aria-live="polite"
      className={cn(
        "inline-flex items-center gap-1 text-xs text-emerald-700 transition-opacity dark:text-emerald-300",
        visible ? "opacity-100" : "opacity-0",
      )}
    >
      {visible && (
        <>
          <Check className="size-3.5" aria-hidden /> Saved
        </>
      )}
    </span>
  );
}
