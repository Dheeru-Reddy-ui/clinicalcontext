"use client";

import { CartesianGrid, Legend, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import type { LoadStage } from "@/lib/evals";

/**
 * Answer latency against concurrent users from the ramp stages of the load
 * test: p50 / p95 / p99 of the pipeline path (cache hits are charted apart
 * because they are a different system), with the error rate on the second
 * axis. The stage that broke is marked.
 */
export function LoadRampChart({ stages, p95LimitMs }: { stages: LoadStage[]; p95LimitMs: number }) {
  const data = stages.map((s) => {
    const ask = s.by_endpoint["ask (pipeline)"] ?? null;
    return {
      users: s.users,
      p50: ask?.p50_ms ?? null,
      p95: ask?.p95_ms ?? null,
      p99: ask?.p99_ms ?? null,
      error_pct: s.total.error_rate === null ? null : Math.round(s.total.error_rate * 1000) / 10,
      broke: s.broke ?? null,
    };
  });
  const broke = data.find((d) => d.broke);
  return (
    <div className="h-64" data-testid="load-ramp-chart">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data} margin={{ top: 8, right: 8, bottom: 4, left: -4 }}>
          <CartesianGrid strokeDasharray="2 4" stroke="var(--border)" vertical={false} />
          <XAxis
            dataKey="users"
            type="number"
            domain={["dataMin", "dataMax"]}
            ticks={data.map((d) => d.users)}
            tick={{ fontSize: 11, fill: "var(--muted-foreground)" }}
            axisLine={false}
            tickLine={false}
            label={{ value: "concurrent users", position: "insideBottom", offset: -2, fontSize: 11, fill: "var(--muted-foreground)" }}
          />
          <YAxis
            yAxisId="ms"
            tickFormatter={(v: number) => (v >= 1000 ? `${(v / 1000).toFixed(1)}s` : `${v}ms`)}
            tick={{ fontSize: 11, fill: "var(--muted-foreground)" }}
            axisLine={false}
            tickLine={false}
          />
          <YAxis
            yAxisId="pct"
            orientation="right"
            domain={[0, 100]}
            tickFormatter={(v: number) => `${v}%`}
            tick={{ fontSize: 11, fill: "var(--muted-foreground)" }}
            axisLine={false}
            tickLine={false}
          />
          <Tooltip
            contentStyle={{ background: "var(--popover)", border: "1px solid var(--border)", borderRadius: 6, fontSize: 12 }}
            formatter={(value, name) => {
              const v = typeof value === "number" ? value : Number(value);
              return name === "error rate" ? `${v}%` : `${Math.round(v)} ms`;
            }}
            labelFormatter={(users) => `${users} users`}
          />
          <Legend wrapperStyle={{ fontSize: 12 }} />
          <ReferenceLine yAxisId="ms" y={p95LimitMs} stroke="var(--destructive)" strokeDasharray="4 4" label={{ value: "p95 limit", fontSize: 10, fill: "var(--destructive)", position: "insideTopLeft" }} />
          {broke && <ReferenceLine yAxisId="ms" x={broke.users} stroke="var(--destructive)" label={{ value: "broke", fontSize: 10, fill: "var(--destructive)", position: "top" }} />}
          <Line yAxisId="ms" type="monotone" dataKey="p50" name="p50" stroke="var(--chart-1)" strokeWidth={2} dot={{ r: 3 }} connectNulls />
          <Line yAxisId="ms" type="monotone" dataKey="p95" name="p95" stroke="var(--chart-2)" strokeWidth={2} dot={{ r: 3 }} connectNulls />
          <Line yAxisId="ms" type="monotone" dataKey="p99" name="p99" stroke="var(--chart-3)" strokeWidth={2} dot={{ r: 3 }} connectNulls />
          <Line yAxisId="pct" type="monotone" dataKey="error_pct" name="error rate" stroke="var(--destructive)" strokeWidth={1.5} strokeDasharray="3 3" dot={{ r: 2 }} connectNulls />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
