"use client";

import { Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { LEG_LABELS, LEG_ORDER, type WaterfallLegs } from "@/lib/voice/protocol";
import { cn } from "@/lib/utils";

/**
 * The latency waterfall (11G.5): one horizontal stacked bar per turn, each
 * segment a leg measured from the moment the user stopped speaking. Legs
 * that did not happen (no LLM call offline, a blocked turn) are absent, not
 * zero-width lies. The same component draws the dashboard's p50/p95 rows.
 */

const LEG_COLORS: Record<keyof WaterfallLegs, string> = {
  endpoint_decision: "var(--chart-1)",
  transcript_final: "var(--chart-2)",
  guardrails: "var(--conflict)",
  retrieval: "var(--chart-3)",
  llm_first_token: "var(--chart-4)",
  first_sentence: "var(--chart-5)",
  tts_ttfb: "var(--citation)",
  client_playback: "var(--muted-foreground)",
};

export interface WaterfallRow {
  label: string;
  legs: Partial<Record<keyof WaterfallLegs, number | null>>;
  total: number | null;
}

export function WaterfallChart({
  rows,
  target,
  className,
  height,
}: {
  rows: WaterfallRow[];
  /** A reference total (e.g. the 1.2 s p50 / 2.0 s p95 targets). */
  target?: number;
  className?: string;
  height?: number;
}) {
  const data = rows.map((row) => ({
    label: row.label,
    total: row.total,
    ...Object.fromEntries(LEG_ORDER.map((leg) => [leg, row.legs[leg] ?? 0])),
  }));
  const present = LEG_ORDER.filter((leg) => rows.some((r) => (r.legs[leg] ?? null) !== null));
  const maxTotal = Math.max(target ?? 0, ...rows.map((r) => r.total ?? 0), 100);

  return (
    <div className={cn("w-full", className)} style={{ height: height ?? Math.max(120, 44 * rows.length + 60) }} data-testid="waterfall-chart">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} layout="vertical" margin={{ top: 4, right: 24, bottom: 4, left: 8 }} barCategoryGap={8}>
          <CartesianGrid strokeDasharray="2 4" stroke="var(--border)" horizontal={false} />
          <XAxis
            type="number"
            domain={[0, Math.ceil(maxTotal / 250) * 250]}
            tickFormatter={(v: number) => `${v} ms`}
            tick={{ fontSize: 11, fill: "var(--muted-foreground)" }}
            axisLine={{ stroke: "var(--border)" }}
            tickLine={false}
          />
          <YAxis
            type="category"
            dataKey="label"
            width={72}
            tick={{ fontSize: 11, fill: "var(--muted-foreground)" }}
            axisLine={false}
            tickLine={false}
          />
          <Tooltip
            cursor={{ fill: "var(--accent)", opacity: 0.4 }}
            content={({ active, payload, label }) => {
              if (!active || !payload?.length) return null;
              const row = payload[0]?.payload as { total: number | null } | undefined;
              return (
                <div className="rounded-md border bg-popover p-3 text-xs shadow-md">
                  <p className="mb-1 font-medium">{String(label)}</p>
                  {payload
                    .filter((p) => typeof p.value === "number" && p.value > 0)
                    .map((p) => (
                      <p key={String(p.dataKey)} className="flex justify-between gap-4">
                        <span>{LEG_LABELS[p.dataKey as keyof WaterfallLegs]}</span>
                        <span className="font-mono">{Math.round(Number(p.value))} ms</span>
                      </p>
                    ))}
                  {row?.total !== null && row?.total !== undefined && (
                    <p className="mt-1 flex justify-between gap-4 border-t pt-1 font-medium">
                      <span>First audio</span>
                      <span className="font-mono">{Math.round(row.total)} ms</span>
                    </p>
                  )}
                </div>
              );
            }}
          />
          <Legend
            iconSize={8}
            wrapperStyle={{ fontSize: 11 }}
            formatter={(value: string) => LEG_LABELS[value as keyof WaterfallLegs] ?? value}
          />
          {present.map((leg) => (
            <Bar key={leg} dataKey={leg} stackId="legs" fill={LEG_COLORS[leg]} isAnimationActive={false} />
          ))}
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
