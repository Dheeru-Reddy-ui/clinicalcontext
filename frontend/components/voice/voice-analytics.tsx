"use client";

import { Mic } from "lucide-react";

import { Skeleton } from "@/components/ui/skeleton";
import { WaterfallChart, type WaterfallRow } from "@/components/voice/waterfall-chart";
import { useVoiceAnalytics } from "@/hooks/use-api";
import { LEG_ORDER, type WaterfallLegs } from "@/lib/voice/protocol";

/**
 * The dashboard's voice section (11G.5, 11D.1, 11D.4, 11F.4): aggregate p50 /
 * p95 per leg as a waterfall, speculation economics, confirmations, barge-in
 * stop time, masks, and the cost of interactivity. Every number is a
 * percentile or a count over this org's voice_turns rows — a leg with no
 * data shows as absent.
 */
export function VoiceAnalytics({ days }: { days: number }) {
  const voice = useVoiceAnalytics(days);
  const data = voice.data;

  const ms = (v: number | null | undefined) => (v === null || v === undefined ? "—" : `${Math.round(v)} ms`);
  const pct = (v: number | null | undefined) => (v === null || v === undefined ? "—" : `${(v * 100).toFixed(0)}%`);

  const rows: WaterfallRow[] = data
    ? (["p50", "p95"] as const).map((p) => ({
        label: p,
        legs: Object.fromEntries(LEG_ORDER.map((leg) => [leg, data.legs[leg]?.[p] ?? null])) as Partial<
          Record<keyof WaterfallLegs, number | null>
        >,
        total: data.total_first_audio[p],
      }))
    : [];

  return (
    <section className="rounded-lg border bg-card" aria-labelledby="voice-analytics-heading">
      <header className="flex flex-wrap items-center justify-between gap-2 border-b px-4 py-2.5">
        <h2 id="voice-analytics-heading" className="flex items-center gap-2 text-sm font-medium">
          <Mic className="size-4" aria-hidden /> Voice · latency waterfall
          <span className="font-mono text-xs font-normal text-muted-foreground">
            {data ? `${data.turns} turns · ${days} d` : ""}
          </span>
        </h2>
        <span className="font-mono text-xs text-muted-foreground">targets p50 ≤ 1.2 s · p95 ≤ 2.0 s first audio</span>
      </header>
      {voice.isPending ? (
        <div className="space-y-2 p-4">
          <Skeleton className="h-6 w-full" />
          <Skeleton className="h-6 w-full" />
        </div>
      ) : !data || data.turns === 0 ? (
        <p className="p-4 text-sm text-muted-foreground">No voice turns in this window yet.</p>
      ) : (
        <>
          <div className="px-2 pt-2">
            <WaterfallChart rows={rows} target={2000} height={150} />
          </div>
          <dl className="grid gap-3 px-4 pb-4 pt-2 text-xs sm:grid-cols-3 lg:grid-cols-6">
            <Stat label="First audio p50 / p95" value={`${ms(data.total_first_audio.p50)} / ${ms(data.total_first_audio.p95)}`} hint={`sent · played ${ms(data.client_first_audio.p50)} / ${ms(data.client_first_audio.p95)}`} />
            <Stat
              label="Speculation hit / wasted"
              value={`${pct(data.speculation_hit_rate)} / ${pct(data.speculation_wasted_rate)}`}
              hint={`${data.speculation_fired} fired · targets ≥ 60% / < 15%`}
            />
            <Stat
              label="Barge-in stop p50 / p95"
              value={`${ms(data.barge_in_stop_ms.p50)} / ${ms(data.barge_in_stop_ms.p95)}`}
              hint={`${data.barge_ins} interruptions · client-side · target p95 ≤ 150 ms`}
            />
            <Stat
              label="LASA confirmations"
              value={String(data.confirmations_requested)}
              hint={`${data.confirmations_resolved_by_voice} by voice · ${data.confirmations_resolved_by_tap} by tap`}
            />
            <Stat label="Masks used" value={String(data.masks_used)} hint="“Checking the guidelines…” past 2 s — a failure indicator" />
            <Stat
              label="Cost of interactivity"
              value={`${data.wasted_output_tokens} tok · ${(data.wasted_audio_ms / 1000).toFixed(1)} s`}
              hint="generation and audio thrown away by cancels"
            />
          </dl>
          <p className="border-t px-4 py-2 text-[11px] text-muted-foreground">
            {data.answered} answered · {data.abstained} abstained · {data.blocked} blocked · {data.corrections_made} medical-term corrections ·{" "}
            {Object.entries(data.by_backend)
              .map(([k, v]) => `${k}: ${v}`)
              .join(", ")}
          </p>
        </>
      )}
    </section>
  );
}

function Stat({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div>
      <dt className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">{label}</dt>
      <dd className="mt-0.5 font-mono text-sm font-semibold tabular-nums">{value}</dd>
      {hint && <dd className="text-[11px] text-muted-foreground">{hint}</dd>}
    </div>
  );
}
