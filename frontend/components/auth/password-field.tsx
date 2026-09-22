"use client";

import { Eye, EyeOff } from "lucide-react";
import { useId, useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { cn } from "@/lib/utils";

/**
 * A password input with a reveal toggle.
 *
 * Typing a password blind is the commonest reason a sign-in fails twice, so
 * the field offers to show it. The toggle is a real button: reachable by
 * keyboard, labelled for screen readers, and it announces which state it is
 * in rather than relying on the icon alone.
 *
 * Its name ("Show password") necessarily contains the field's own label, so
 * anything addressing the input by label must say so exactly — the e2e
 * helpers pass `{ exact: true }`.
 */
export function PasswordField({
  label,
  value,
  onChange,
  autoComplete,
  hint,
  minLength,
  autoFocus,
  required = true,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  autoComplete: "current-password" | "new-password";
  hint?: string;
  minLength?: number;
  autoFocus?: boolean;
  required?: boolean;
}) {
  const id = useId();
  const hintId = `${id}-hint`;
  const [revealed, setRevealed] = useState(false);

  return (
    <div className="flex flex-col gap-2">
      <Label htmlFor={id}>{label}</Label>
      <div className="relative">
        <Input
          id={id}
          type={revealed ? "text" : "password"}
          autoComplete={autoComplete}
          minLength={minLength}
          autoFocus={autoFocus}
          required={required}
          aria-describedby={hint ? hintId : undefined}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          className={cn("pr-10")}
        />
        <Button
          type="button"
          variant="ghost"
          size="icon"
          className="absolute right-0 top-0 size-9 text-muted-foreground hover:bg-transparent hover:text-foreground"
          aria-pressed={revealed}
          aria-label={revealed ? "Hide password" : "Show password"}
          onClick={() => setRevealed((shown) => !shown)}
        >
          {revealed ? (
            <EyeOff className="size-4" aria-hidden />
          ) : (
            <Eye className="size-4" aria-hidden />
          )}
        </Button>
      </div>
      {hint && (
        <p id={hintId} className="text-xs text-muted-foreground">
          {hint}
        </p>
      )}
    </div>
  );
}
