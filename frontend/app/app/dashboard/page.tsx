"use client";

import { useState } from "react";
import { Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { ErrorState, PageBody, PageHeader } from "@/components/clinical/page";
import { useAuth } from "@/components/providers/auth-provider";
import { Skeleton } from "@/components/ui/skeleton";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { CostCard } from "@/components/dashboard/cost-card";
import { CalibrationCard } from "@/components/evals/calibration-card";
import { ReviewQueue } from "@/components/evals/review-queue";
import { VoiceAnalytics } from "@/components/voice/voice-analytics";
import { useAnalytics } from "@/hooks/use-api";
import { formatMs, formatUsd } from "@/lib/text";
import { cn } from "@/lib/utils";

const pct = (v: number | null | undefined) => (v === null || v === undefined ? "—" : `${(v * 100).toFixed(0)}%`);

export default function DashboardPage() {
  const { role } = useAuth();
  const [days, setDays] = useState(30);
  const { overview, usage, quality, cost } = useAnalytics(days);

  if (role === "viewer") {
    return (
      <PageBody>
        <ErrorState error={Object.assign(new Error("The dashboard is available to owners and clinicians."), { status: 403 })} />
      </PageBody>
    );
  }

  const o = overview.data;
  const q = quality.data;
  const costPerQuery = o && o.total_queries > 0 ? o.total_cost_usd / o.total_queries : null;
  const feedbackTotal = q ? q.feedback_up + q.feedback_down : 0;
  const thumbsDown = q && feedbackTotal > 0 ? q.feedback_down / feedbackTotal : null;

  return (
    <PageBody>
      <PageHeader
        title="Dashboard"
        description="How the organisation is using the tool, and how honest its answers are."
        actions={
          <ToggleGroup value={[String(days)]} onValueChange={(v) => { const n = Number(Array.isArray(v) ? v[0] : v); if (n) setDays(n); }} aria-label="Window">
            <ToggleGroupItem value="7">7d</ToggleGroupItem>
            <ToggleGroupItem value="30">30d</ToggleGroupItem>
            <ToggleGroupItem value="90">90d</ToggleGroupItem>
          </ToggleGroup>
        }
      />

      {overview.isError ? (
        <ErrorState error={overview.error} onRetry={() => void overview.refetch()} />
      ) : (
        <section className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4" aria-label="Key metrics">
          <Stat label="Queries" value={o ? o.total_queries.toLocaleString() : null} hint={o ? `${o.answered} answered · ${o.abstained} abstained · ${o.blocked} blocked` : undefined} />
          <Stat label="Latency p50 / p95" value={o ? `${formatMs(o.latency_p50_ms)} / ${formatMs(o.latency_p95_ms)}` : null} />
          <Stat label="Cost per query" value={o ? formatUsd(costPerQuery) : null} hint={o ? `${formatUsd(o.total_cost_usd)} total · ${formatUsd(o.cost_saved_usd)} saved by cache` : undefined} />
          <Stat label="Cache-hit rate" value={o ? pct(o.cache_hit_rate) : null} hint={o ? `${o.cache_hits} hits` : undefined} />
          <Stat label="Abstention rate" value={o ? pct(o.abstention_rate) : null} tone="neutral" hint="Abstaining is correct when the evidence is thin." />
          <Stat label="Contradictions surfaced" value={o ? pct(o.contradiction_rate) : null} hint="Share of answers where sources disagreed." />
          <Stat
            label="Guardrail triggers"
            value={o ? String(o.guardrail_triggers.phi + o.guardrail_triggers.scope + o.guardrail_triggers.red_flag) : null}
            hint={o ? `PHI ${o.guardrail_triggers.phi} · scope ${o.guardrail_triggers.scope} · red flag ${o.guardrail_triggers.red_flag}` : undefined}
          />
          <Stat label="Thumbs-down rate" value={q ? pct(thumbsDown) : null} hint={q ? `${q.feedback_up} up · ${q.feedback_down} down` : undefined} />
        </section>
      )}

      <div className="grid gap-4 lg:grid-cols-2">
        <section className="rounded-lg border bg-card p-4" aria-labelledby="vol-heading">
          <h2 id="vol-heading" className="mb-3 text-sm font-medium">Query volume · daily</h2>
          {usage.isPending ? (
            <Skeleton className="h-56 w-full" />
          ) : usage.isError ? (
            <ErrorState error={usage.error} />
          ) : (
            <div className="h-56">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={usage.data.points} margin={{ top: 8, right: 8, bottom: 0, left: -16 }}>
                  <CartesianGrid strokeDasharray="2 4" stroke="var(--border)" vertical={false} />
                  <XAxis dataKey="day" tickFormatter={(d: string) => d.slice(5)} tick={{ fontSize: 11, fill: "var(--muted-foreground)" }} axisLine={false} tickLine={false} />
                  <YAxis allowDecimals={false} tick={{ fontSize: 11, fill: "var(--muted-foreground)" }} axisLine={false} tickLine={false} />
                  <Tooltip cursor={{ fill: "var(--accent)" }} contentStyle={{ background: "var(--popover)", border: "1px solid var(--border)", borderRadius: 6, fontSize: 12 }} />
                  <Legend wrapperStyle={{ fontSize: 12 }} />
                  <Bar dataKey="queries" name="Queries" fill="var(--chart-1)" radius={[2, 2, 0, 0]} />
                  <Bar dataKey="cache_hits" name="Cache hits" fill="var(--chart-5)" radius={[2, 2, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
            </div>
          )}
        </section>

        <section className="rounded-lg border bg-card p-4" aria-labelledby="cost-heading">
          <h2 id="cost-heading" className="mb-3 text-sm font-medium">Cost · daily (USD)</h2>
          {usage.isPending ? (
            <Skeleton className="h-56 w-full" />
          ) : usage.isError ? (
            <ErrorState error={usage.error} />
          ) : (
            <div className="h-56">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={usage.data.points} margin={{ top: 8, right: 8, bottom: 0, left: -8 }}>
                  <CartesianGrid strokeDasharray="2 4" stroke="var(--border)" vertical={false} />
                  <XAxis dataKey="day" tickFormatter={(d: string) => d.slice(5)} tick={{ fontSize: 11, fill: "var(--muted-foreground)" }} axisLine={false} tickLine={false} />
                  <YAxis tickFormatter={(v: number) => `$${v.toFixed(2)}`} tick={{ fontSize: 11, fill: "var(--muted-foreground)" }} axisLine={false} tickLine={false} />
                  <Tooltip formatter={(v) => formatUsd(typeof v === "number" ? v : Number(v))} cursor={{ fill: "var(--accent)" }} contentStyle={{ background: "var(--popover)", border: "1px solid var(--border)", borderRadius: 6, fontSize: 12 }} />
                  <Legend wrapperStyle={{ fontSize: 12 }} />
                  <Bar dataKey="cost_usd" name="Spent" fill="var(--chart-3)" radius={[2, 2, 0, 0]} />
                  <Bar dataKey="cost_saved_usd" name="Saved by cache" fill="var(--chart-2)" radius={[2, 2, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
            </div>
          )}
        </section>
      </div>

      {q && (
        <div className="grid gap-4 lg:grid-cols-3">
          <Distribution title="By confidence" data={q.by_confidence} order={["high", "moderate", "low"]} />
          <Distribution title="By evidence grade" data={q.by_evidence_grade} order={["A", "B", "C", "D", "none"]} />
          <Distribution title="Thumbs-down reasons" data={q.feedback_by_reason} />
        </div>
      )}

      <CostCard report={cost} />

      <VoiceAnalytics days={days} />

      <div className="grid gap-4 lg:grid-cols-2">
        <CalibrationCard />
        <ReviewQueue enabled={role === "owner"} />
      </div>

      {q && q.top_queries.length > 0 && (
        <section className="rounded-lg border bg-card" aria-labelledby="top-heading">
          <h2 id="top-heading" className="border-b px-4 py-2.5 text-sm font-medium">Most asked</h2>
          <ol className="divide-y">
            {q.top_queries.map((t) => (
              <li key={t.query} className="flex items-center gap-3 px-4 py-2 text-sm">
                <span className="min-w-0 flex-1 truncate">{t.query}</span>
                <span className="font-mono text-xs text-muted-foreground">×{t.count}</span>
              </li>
            ))}
          </ol>
        </section>
      )}
    </PageBody>
  );
}

function Stat({ label, value, hint, tone }: { label: string; value: string | null; hint?: string; tone?: "neutral" }) {
  return (
    <div className={cn("rounded-lg border bg-card p-4", tone === "neutral" && "border-dashed")}>
      <p className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">{label}</p>
      {value === null ? <Skeleton className="mt-1 h-7 w-24" /> : <p className="mt-1 font-mono text-2xl font-semibold tabular-nums">{value}</p>}
      {hint && <p className="mt-1 text-xs text-muted-foreground">{hint}</p>}
    </div>
  );
}

function Distribution({ title, data, order }: { title: string; data: Record<string, number>; order?: string[] }) {
  const entries = Object.entries(data).sort((a, b) => (order ? order.indexOf(a[0]) - order.indexOf(b[0]) : b[1] - a[1]));
  const total = entries.reduce((s, [, n]) => s + n, 0);
  return (
    <section className="rounded-lg border bg-card p-4">
      <h2 className="mb-2 text-sm font-medium">{title}</h2>
      {total === 0 ? (
        <p className="text-xs text-muted-foreground">No data in this window.</p>
      ) : (
        <ul className="space-y-1.5">
          {entries.map(([k, n]) => (
            <li key={k} className="text-xs">
              <div className="flex justify-between">
                <span className="capitalize">{k.replace(/_/g, " ")}</span>
                <span className="font-mono text-muted-foreground">{n} · {((n / total) * 100).toFixed(0)}%</span>
              </div>
              <div className="mt-0.5 h-1.5 rounded-full bg-muted">
                <div className="h-1.5 rounded-full bg-primary" style={{ width: `${(n / total) * 100}%` }} aria-hidden />
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
