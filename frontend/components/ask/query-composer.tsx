"use client";

import { ArrowUp, Columns3, ListTree, Square, X } from "lucide-react";
import { useCallback, useEffect, useId, useMemo, useRef, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Kbd } from "@/components/ui/kbd";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Toggle } from "@/components/ui/toggle";
import { useToken } from "@/hooks/use-api";
import { api } from "@/lib/api-client";
import type { Schemas } from "@/lib/domain";
import type { QueryRequest } from "@/lib/stream";
import { cn } from "@/lib/utils";

type Pico = NonNullable<Schemas["PICO"]>;

const EMPTY_PICO: Pico = { population: null, intervention: null, comparison: null, outcome: null };

function picoFilled(p: Pico): boolean {
  return Boolean(p.population || p.intervention || p.comparison || p.outcome);
}

/** Render the parsed structure as a chip: "P: adults with AF · I: apixaban · …" */
function picoSummary(p: Pico): string {
  return (
    [
      p.population && `P: ${p.population}`,
      p.intervention && `I: ${p.intervention}`,
      p.comparison && `C: ${p.comparison}`,
      p.outcome && `O: ${p.outcome}`,
    ]
      .filter(Boolean)
      .join("  ·  ") || ""
  );
}

interface QueryComposerProps {
  busy: boolean;
  onSubmit: (request: QueryRequest) => void;
  onCancel: () => void;
  sessionId?: string | null;
  /** Prefill (e.g. "ask again" from history). */
  initialQuery?: string;
  compact?: boolean;
  autoFocus?: boolean;
}

export function QueryComposer({
  busy,
  onSubmit,
  onCancel,
  sessionId = null,
  initialQuery = "",
  compact = false,
  autoFocus = true,
}: QueryComposerProps) {
  const token = useToken();
  const [query, setQuery] = useState(initialQuery);
  const [picoOpen, setPicoOpen] = useState(false);
  const [pico, setPico] = useState<Pico>(EMPTY_PICO);
  const [compare, setCompare] = useState(false);
  const [entities, setEntities] = useState<string[]>([]);
  const [entityDraft, setEntityDraft] = useState("");
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const listId = useId();

  // -- autocomplete (MeSH + this org's history; sub-50 ms on the backend) ----------
  const [suggestions, setSuggestions] = useState<Schemas["Suggestion"][]>([]);
  const [highlighted, setHighlighted] = useState(-1);
  const [focused, setFocused] = useState(false);
  useEffect(() => {
    const q = query.trim();
    // Completion helps while a term is being typed; a formed question doesn't need it.
    const formed = q.endsWith("?") || q.split(/\s+/).length > 6;
    if (!token || !focused || q.length < 3 || busy || formed) {
      setSuggestions([]);
      return;
    }
    const handle = window.setTimeout(() => {
      api
        .suggest(token, q)
        .then((out) => {
          setSuggestions(out.suggestions.filter((s) => s.value.toLowerCase() !== q.toLowerCase()));
          setHighlighted(-1);
        })
        .catch(() => setSuggestions([]));
    }, 150);
    return () => window.clearTimeout(handle);
  }, [query, token, busy, focused]);

  const picoActive = picoFilled(pico);
  const canSubmit = query.trim().length > 0 && !busy && (!compare || entities.length >= 2);

  const submit = useCallback(() => {
    if (!canSubmit) return;
    setSuggestions([]);
    onSubmit({
      query: query.trim(),
      session_id: sessionId,
      mode: compare ? "comparison" : "standard",
      entities: compare ? entities : [],
      pico: picoActive ? pico : null,
    });
  }, [canSubmit, onSubmit, query, sessionId, compare, entities, picoActive, pico]);

  const onKeyDown = (event: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (suggestions.length > 0) {
      if (event.key === "ArrowDown") {
        event.preventDefault();
        setHighlighted((h) => Math.min(h + 1, suggestions.length - 1));
        return;
      }
      if (event.key === "ArrowUp") {
        event.preventDefault();
        setHighlighted((h) => Math.max(h - 1, -1));
        return;
      }
      if (event.key === "Escape") {
        setSuggestions([]);
        return;
      }
      if (event.key === "Tab" && highlighted >= 0) {
        event.preventDefault();
        setQuery(suggestions[highlighted]?.value ?? query);
        setSuggestions([]);
        return;
      }
    }
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      if (highlighted >= 0 && suggestions[highlighted]) {
        setQuery(suggestions[highlighted].value);
        setSuggestions([]);
        return;
      }
      submit();
    }
  };

  const addEntity = () => {
    const value = entityDraft.trim();
    if (!value || entities.includes(value) || entities.length >= 8) return;
    setEntities([...entities, value]);
    setEntityDraft("");
  };

  const picoChip = useMemo(() => (picoActive ? picoSummary(pico) : ""), [pico, picoActive]);

  return (
    <div className={cn("rounded-lg border bg-card shadow-xs", compact ? "p-2" : "p-3")}>
      {picoOpen && (
        <fieldset className="mb-3 grid grid-cols-2 gap-2 rounded-md border bg-muted/40 p-3 md:grid-cols-4">
          <legend className="px-1 text-xs font-medium text-muted-foreground">
            PICO — structure the question
          </legend>
          {(
            [
              ["population", "Population", "e.g. adults with non-valvular AF"],
              ["intervention", "Intervention", "e.g. apixaban"],
              ["comparison", "Comparison", "e.g. warfarin"],
              ["outcome", "Outcome", "e.g. stroke prevention"],
            ] as const
          ).map(([key, label, placeholder]) => (
            <div key={key} className="flex flex-col gap-1">
              <Label htmlFor={`pico-${key}`} className="text-xs">
                {label}
              </Label>
              <Input
                id={`pico-${key}`}
                value={pico[key] ?? ""}
                placeholder={placeholder}
                onChange={(e) => setPico({ ...pico, [key]: e.target.value || null })}
                className="h-8 text-sm"
              />
            </div>
          ))}
        </fieldset>
      )}

      {compare && (
        <div className="mb-3 flex flex-wrap items-center gap-1.5 rounded-md border bg-muted/40 p-2">
          <span className="mr-1 text-xs font-medium text-muted-foreground">Compare</span>
          {entities.map((e) => (
            <Badge key={e} variant="secondary" className="gap-1 pr-1">
              {e}
              <button
                type="button"
                aria-label={`Remove ${e}`}
                onClick={() => setEntities(entities.filter((x) => x !== e))}
                className="rounded-sm hover:bg-foreground/10"
              >
                <X className="size-3" />
              </button>
            </Badge>
          ))}
          <Input
            value={entityDraft}
            placeholder={entities.length < 2 ? "add at least two options…" : "add another…"}
            onChange={(e) => setEntityDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" || e.key === ",") {
                e.preventDefault();
                addEntity();
              }
            }}
            onBlur={addEntity}
            className="h-7 w-48 text-sm"
            aria-label="Option to compare"
          />
        </div>
      )}

      <div>
        <Textarea
          ref={textareaRef}
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={onKeyDown}
          onFocus={() => setFocused(true)}
          onBlur={() => window.setTimeout(() => setFocused(false), 120)}
          placeholder={
            compare
              ? "What should these options be compared on? e.g. stroke prevention in AF"
              : "Ask a clinical question — e.g. first-line anticoagulation in non-valvular atrial fibrillation"
          }
          rows={compact ? 2 : 3}
          autoFocus={autoFocus}
          disabled={busy}
          aria-label="Clinical question"
          aria-describedby={suggestions.length ? listId : undefined}
          className="resize-none border-0 bg-transparent p-1 text-base shadow-none focus-visible:ring-0"
        />
        {suggestions.length > 0 && (
          <ul
            id={listId}
            role="listbox"
            aria-label="Suggestions"
            aria-live="polite"
            className="mt-1 max-h-56 overflow-y-auto rounded-md border bg-popover p-1 shadow-sm"
          >
            {suggestions.map((s, i) => (
              <li
                key={`${s.kind}-${s.value}`}
                role="option"
                aria-selected={i === highlighted}
                onMouseDown={(e) => {
                  e.preventDefault();
                  setQuery(s.value);
                  setSuggestions([]);
                  textareaRef.current?.focus();
                }}
                className={cn(
                  "flex cursor-pointer items-center justify-between gap-3 rounded-sm px-2 py-1.5 text-sm",
                  i === highlighted ? "bg-accent text-accent-foreground" : "hover:bg-accent/60",
                )}
              >
                <span className="truncate">{s.value}</span>
                <span className="shrink-0 text-[10px] uppercase tracking-wide text-muted-foreground">
                  {s.kind}
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>

      <div className="mt-2 flex flex-wrap items-center gap-1.5">
        <Toggle
          size="sm"
          pressed={picoOpen}
          onPressedChange={setPicoOpen}
          className="gap-1.5 text-xs"
        >
          <ListTree className="size-3.5" /> PICO
        </Toggle>
        <Toggle
          size="sm"
          pressed={compare}
          onPressedChange={(v) => {
            setCompare(v);
            if (!v) setEntities([]);
          }}
          className="gap-1.5 text-xs"
        >
          <Columns3 className="size-3.5" /> Compare
        </Toggle>
        {picoChip && (
          <Badge variant="outline" className="max-w-full truncate font-mono text-[11px]" title={picoChip}>
            PICO mode · {picoChip}
          </Badge>
        )}
        <span className="ml-auto hidden text-[11px] text-muted-foreground sm:inline">
          <Kbd>Enter</Kbd> to ask · <Kbd>Shift</Kbd>+<Kbd>Enter</Kbd> newline
        </span>
        {busy ? (
          <Button size="sm" variant="outline" onClick={onCancel} aria-label="Stop">
            <Square className="size-3.5" /> Stop
          </Button>
        ) : (
          <Button size="sm" onClick={submit} disabled={!canSubmit} aria-label="Ask">
            <ArrowUp className="size-3.5" /> Ask
          </Button>
        )}
      </div>
    </div>
  );
}
