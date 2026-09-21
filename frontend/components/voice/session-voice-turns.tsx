"use client";

import { Mic } from "lucide-react";

import { WaterfallChart, type WaterfallRow } from "@/components/voice/waterfall-chart";
import { useVoiceTurns } from "@/hooks/use-api";
import type { WaterfallLegs } from "@/lib/voice/protocol";

/**
 * The per-turn waterfall in the session view (11G.5 — "debug for you"):
 * every spoken turn of this thread with its legs, outcome, corrections and
 * whether speculation paid off.
 */
export function SessionVoiceTurns({ sessionId }: { sessionId: string }) {
  const turns = useVoiceTurns(sessionId);
  const items = turns.data?.items ?? [];
  if (items.length === 0) return null;

  const ordered = [...items].sort((a, b) => a.turn_index - b.turn_index || a.created_at.localeCompare(b.created_at));
  const latencyOf = (t: (typeof items)[number]) => t.latency ?? {};
  const rows: WaterfallRow[] = ordered
    .filter((t) => typeof latencyOf(t).total_first_audio_ms === "number")
    .map((t) => {
      const latency = latencyOf(t);
      const legs = (latency.legs ?? {}) as Partial<Record<keyof WaterfallLegs, number | null>>;
      const played = latency.client_first_audio_ms;
      return {
        label: `turn ${t.turn_index + 1}`,
        legs,
        total: typeof played === "number" ? played : (latency.total_first_audio_ms as number),
      };
    });

  return (
    <section className="rounded-lg border bg-card" aria-labelledby="session-voice-heading">
      <header className="flex items-center justify-between border-b px-4 py-2.5">
        <h2 id="session-voice-heading" className="flex items-center gap-2 text-sm font-medium">
          <Mic className="size-4" aria-hidden /> Voice turns · latency waterfall
        </h2>
        <span className="font-mono text-xs text-muted-foreground">{items.length} spoken</span>
      </header>
      {rows.length > 0 && (
        <div className="px-2 pt-2">
          <WaterfallChart rows={rows} target={2000} />
        </div>
      )}
      <ul className="divide-y text-xs">
        {ordered.map((t) => (
          <li key={t.id} className="grid gap-1 px-4 py-2 sm:grid-cols-[6rem_1fr]">
            <span className="font-mono text-muted-foreground">turn {t.turn_index + 1}</span>
            <span className="min-w-0">
              <span className="block truncate">{t.transcript_final || t.transcript_raw || "—"}</span>
              <span className="block font-mono text-[11px] text-muted-foreground">
                {t.outcome}
                {typeof latencyOf(t).total_first_audio_ms === "number" &&
                  ` · first audio ${Math.round(latencyOf(t).total_first_audio_ms as number)} ms`}
                {t.speculation?.fired === true && ` · speculation ${t.speculation.hit ? "hit" : "miss"}`}
                {(t.corrections?.length ?? 0) > 0 && ` · ${t.corrections?.length} correction${t.corrections?.length === 1 ? "" : "s"}`}
                {t.confirmation?.requested === true && ` · confirmed ${String(t.confirmation.chosen ?? "(pending)")}`}
                {typeof t.barge_in?.count === "number" && t.barge_in.count > 0 && ` · interrupted`}
                {t.mask_used && " · mask used"}
                {` · ${t.stt_model} → ${t.tts_model}`}
              </span>
            </span>
          </li>
        ))}
      </ul>
    </section>
  );
}
