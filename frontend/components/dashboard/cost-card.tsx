"use client";

import { Coins } from "lucide-react";
import { Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { ErrorState } from "@/components/clinical/page";
import { Skeleton } from "@/components/ui/skeleton";
import type { Schemas } from "@/lib/domain";
import { formatUsd } from "@/lib/text";

type CostReport = Schemas["CostReport"];
type CostLine = Schemas["CostLine"];

const COMPONENTS: { key: string; label: string }[] = [
  { key: "embedding", label: "Embedding" },
  { key: "rerank", label: "Rerank" },
  { key: "generation", label: "Generation" },
  { key: "stt", label: "Speech-to-text" },
  { key: "tts", label: "Text-to-speech" },
];

const PROVIDER_LABEL: Record<string, string> = {
  cohere: "Cohere",
  anthropic: "Anthropic",
  deepgram: "Deepgram",
  elevenlabs: "ElevenLabs",
  local: "local (offline)",
};

function unitsLabel(line: CostLine): string {
  const n = line.units >= 1000 ? `${(line.units / 1000).toFixed(1)}k` : line.units.toFixed(line.unit === "seconds" ? 1 : 0);
  return `${n} ${line.unit.replace("_", " ")}`;
}

/**
 * Spend by component (Phase 13), read from the cost ledger: every embedding,
 * rerank, generation, STT and TTS call the tenant made, priced at the
 * published list price, next to what the caches avoided. On the offline
 * backend the real spend is $0 and the same units are shown at the cloud
 * list price of the provider each stand-in replaces — labelled a projection,
 * never presented as a bill.
 */
export function CostCard({ report }: { report: { data?: CostReport; isPending: boolean; isError: boolean; error: Error | null } }) {
  const data = report.data;
  const projected = data?.offline ?? false;
  const totalKey = projected ? "projected_usd" : "cost_usd";
  const savedKey = projected ? "saved_projected_usd" : "saved_usd";
  const monthSaved = data
    ? projected
      ? data.month_semantic_cache_saved_projected_usd
      : data.month_semantic_cache_saved_usd
    : null;

  return (
    <section className="rounded-lg border bg-card" aria-labelledby="spend-heading">
      <header className="flex flex-wrap items-center justify-between gap-2 border-b px-4 py-2.5">
        <h2 id="spend-heading" className="flex items-center gap-2 text-sm font-medium">
          <Coins className="size-4" aria-hidden /> Spend by component
        </h2>
        {data && (
          <span className="font-mono text-xs text-muted-foreground">
            {projected ? "offline backend · $0 real · projected at cloud list price" : "list prices"} · checked {data.price_checked}
          </span>
        )}
      </header>
      {report.isPending ? (
        <div className="space-y-2 p-4">
          <Skeleton className="h-6 w-full" />
          <Skeleton className="h-40 w-full" />
        </div>
      ) : report.isError || !data ? (
        <div className="p-4">
          <ErrorState error={report.error ?? new Error("No cost report")} />
        </div>
      ) : (
        <div className="space-y-4 p-4">
          <p className="rounded-md border border-dashed px-3 py-2 text-sm" data-testid="cache-savings">
            Semantic caching avoided <strong className="font-mono">{formatUsd(monthSaved)}</strong> this month
            {projected && <span className="text-muted-foreground"> (at cloud list price — the offline backend spent $0)</span>}
            {" · "}
            <span className="text-muted-foreground">
              last {data.days} days: {formatUsd(projected ? data.semantic_cache_saved_projected_usd : data.semantic_cache_saved_usd)} semantic,{" "}
              {formatUsd(projected ? data.embedding_cache_saved_projected_usd : data.embedding_cache_saved_usd)} embedding cache
            </span>
          </p>

          <dl className="grid grid-cols-2 gap-3 sm:grid-cols-5">
            {COMPONENTS.map((c) => {
              const actual = data.by_component[c.key] ?? 0;
              const proj = data.by_component_projected[c.key] ?? 0;
              return (
                <div key={c.key} className="rounded-md border px-3 py-2">
                  <dt className="text-xs text-muted-foreground">{c.label}</dt>
                  <dd className="font-mono text-base tabular-nums">{formatUsd(projected ? proj : actual)}</dd>
                  {projected && <dd className="text-[11px] text-muted-foreground">real {formatUsd(actual)}</dd>}
                </div>
              );
            })}
          </dl>

          <div className="h-44">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={data.series} margin={{ top: 8, right: 8, bottom: 0, left: -8 }}>
                <CartesianGrid strokeDasharray="2 4" stroke="var(--border)" vertical={false} />
                <XAxis dataKey="day" tickFormatter={(d: string) => d.slice(5)} tick={{ fontSize: 11, fill: "var(--muted-foreground)" }} axisLine={false} tickLine={false} />
                <YAxis tickFormatter={(v: number) => `$${v.toFixed(3)}`} tick={{ fontSize: 11, fill: "var(--muted-foreground)" }} axisLine={false} tickLine={false} />
                <Tooltip formatter={(v) => formatUsd(typeof v === "number" ? v : Number(v))} cursor={{ fill: "var(--accent)" }} contentStyle={{ background: "var(--popover)", border: "1px solid var(--border)", borderRadius: 6, fontSize: 12 }} />
                <Legend wrapperStyle={{ fontSize: 12 }} />
                <Bar dataKey={totalKey} name={projected ? "Projected spend" : "Spent"} fill="var(--chart-3)" radius={[2, 2, 0, 0]} />
                <Bar dataKey={savedKey} name="Avoided by caches" fill="var(--chart-2)" radius={[2, 2, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>

          {data.lines.length > 0 && (
            <table className="w-full text-xs">
              <thead className="text-muted-foreground">
                <tr className="text-left">
                  <th className="py-1 font-medium">Component</th>
                  <th className="py-1 font-medium">Provider · model</th>
                  <th className="py-1 text-right font-medium">Calls</th>
                  <th className="py-1 text-right font-medium">Units</th>
                  <th className="py-1 text-right font-medium">{projected ? "Projected" : "Cost"}</th>
                  <th className="py-1 text-right font-medium">Avoided</th>
                </tr>
              </thead>
              <tbody className="font-mono tabular-nums">
                {data.lines.map((line) => (
                  <tr key={`${line.component}-${line.provider}-${line.model}-${line.unit}`} className="border-t">
                    <td className="py-1 font-sans">{COMPONENTS.find((c) => c.key === line.component)?.label ?? line.component}</td>
                    <td className="py-1 font-sans text-muted-foreground">
                      {PROVIDER_LABEL[line.provider] ?? line.provider} · {line.model}
                    </td>
                    <td className="py-1 text-right">{line.calls}</td>
                    <td className="py-1 text-right">{unitsLabel(line)}</td>
                    <td className="py-1 text-right">{formatUsd(projected ? line.projected_usd : line.cost_usd)}</td>
                    <td className="py-1 text-right text-muted-foreground">
                      {line.cached_units > 0 ? formatUsd(projected ? line.cached_projected_usd : line.cached_saved_usd) : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}
    </section>
  );
}
