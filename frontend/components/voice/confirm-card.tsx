"use client";

import { HelpCircle } from "lucide-react";

import { Button } from "@/components/ui/button";
import type { VoiceTurn } from "@/hooks/use-voice-session";

/**
 * The LASA confirmation as a visible choice (11G.2): the agent asked by
 * voice; the clinician can answer by voice or tap. Never a silent guess.
 */
export function ConfirmCard({
  turn,
  onChoose,
}: {
  turn: VoiceTurn;
  onChoose: (name: string) => void;
}) {
  const confirm = turn.confirm;
  if (!confirm) return null;
  return (
    <section
      className="rounded-lg border border-conflict/40 bg-conflict-bg/50 p-4"
      role="group"
      aria-labelledby="confirm-heading"
      data-testid="lasa-confirm"
    >
      <div className="flex items-start gap-3">
        <span className="grid size-8 shrink-0 place-items-center rounded-md bg-conflict-bg text-conflict-fg">
          <HelpCircle className="size-4" aria-hidden />
        </span>
        <div className="min-w-0 flex-1">
          <h2 id="confirm-heading" className="text-sm font-semibold text-conflict-fg">
            Just to be safe — which drug did you mean?
          </h2>
          <p className="mt-0.5 text-sm text-foreground/80">
            Heard <span className="font-mono">“{confirm.heard}”</span>, which sits on the ISMP confused-drug-names list.
          </p>
          <div className="mt-3 grid gap-2 sm:grid-cols-2">
            {confirm.options.map((option) => (
              <Button
                key={option.name}
                variant="outline"
                className="h-auto justify-start whitespace-normal py-2 text-left"
                onClick={() => onChoose(option.name)}
              >
                <span>
                  <span className="block font-semibold">{option.name}</span>
                  <span className="block text-xs text-muted-foreground">{option.description}</span>
                </span>
              </Button>
            ))}
          </div>
        </div>
      </div>
    </section>
  );
}
