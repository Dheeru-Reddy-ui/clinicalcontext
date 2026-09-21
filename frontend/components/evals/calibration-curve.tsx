"use client";

import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Scatter,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import type { CalibrationCurve } from "@/lib/evals";
import { num, pct } from "@/lib/evals";

/**
 * A reliability diagram: stated confidence (what the system means by
 * "high" / "moderate" / "low", as a probability) against how often such
 * answers turned out right. The diagonal is perfect calibration; a point
 * below it is over-confidence. Bucket size is written next to each point
 * because three points with n=4 tell you nothing.
 */
export function CalibrationCurveChart({ curve, height = 200 }: { curve: CalibrationCurve; height?: number }) {
  const points = curve.buckets
    .filter((b) => b.observed !== null && b.n > 0)
    .map((b) => ({ level: b.level, predicted: b.predicted, observed: b.observed as number, n: b.n }));
  if (points.length === 0) {
    return <p className="p-4 text-sm text-muted-foreground">No rated answers to calibrate against yet.</p>;
  }
  const diagonal = [
    { predicted: 0, observed: 0 },
    { predicted: 1, observed: 1 },
  ];
  return (
    <div style={{ height }} data-testid="calibration-curve">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart margin={{ top: 12, right: 16, bottom: 4, left: -12 }}>
          <CartesianGrid strokeDasharray="2 4" stroke="var(--border)" />
          <XAxis
            type="number"
            dataKey="predicted"
            domain={[0, 1]}
            ticks={[0, 0.25, 0.5, 0.75, 1]}
            tickFormatter={(v: number) => `${Math.round(v * 100)}%`}
            tick={{ fontSize: 11, fill: "var(--muted-foreground)" }}
            axisLine={false}
            tickLine={false}
            label={{ value: "stated", position: "insideBottomRight", fontSize: 11, fill: "var(--muted-foreground)", dy: 8 }}
          />
          <YAxis
            type="number"
            dataKey="observed"
            domain={[0, 1]}
            ticks={[0, 0.25, 0.5, 0.75, 1]}
            tickFormatter={(v: number) => `${Math.round(v * 100)}%`}
            tick={{ fontSize: 11, fill: "var(--muted-foreground)" }}
            axisLine={false}
            tickLine={false}
          />
          <Tooltip
            cursor={false}
            contentStyle={{ background: "var(--popover)", border: "1px solid var(--border)", borderRadius: 6, fontSize: 12 }}
            formatter={(value, name) => [pct(typeof value === "number" ? value : Number(value)), name === "observed" ? "observed" : "stated"]}
            labelFormatter={(_, payload) => {
              const p = payload?.[0]?.payload as { level?: string; n?: number } | undefined;
              return p?.level ? `${p.level} · n=${p.n}` : "";
            }}
          />
          <ReferenceLine segment={[{ x: 0, y: 0 }, { x: 1, y: 1 }]} stroke="var(--muted-foreground)" strokeDasharray="4 4" />
          <Line data={diagonal} dataKey="observed" stroke="transparent" dot={false} isAnimationActive={false} legendType="none" />
          <Scatter data={points} dataKey="observed" fill="var(--chart-1)" isAnimationActive={false} />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

export function CalibrationTable({ curve }: { curve: CalibrationCurve }) {
  return (
    <table className="w-full text-xs">
      <thead>
        <tr className="border-b text-left text-muted-foreground">
          <th className="py-1 pr-3 font-medium">Stated</th>
          <th className="py-1 pr-3 font-medium">n</th>
          <th className="py-1 pr-3 font-medium">Meant</th>
          <th className="py-1 pr-3 font-medium">Observed</th>
          <th className="py-1 font-medium">Gap</th>
        </tr>
      </thead>
      <tbody>
        {curve.buckets.map((b) => (
          <tr key={b.level} className="border-b border-border/60 font-mono tabular-nums">
            <td className="py-1 pr-3 font-sans">{b.level}</td>
            <td className="py-1 pr-3">{b.n}</td>
            <td className="py-1 pr-3">{pct(b.predicted)}</td>
            <td className="py-1 pr-3">{pct(b.observed)}</td>
            <td className={`py-1 ${b.gap !== null && b.gap < -0.15 ? "text-destructive" : ""}`}>
              {b.gap === null ? "—" : `${b.gap > 0 ? "+" : ""}${(b.gap * 100).toFixed(0)} pts`}
            </td>
          </tr>
        ))}
        <tr className="font-mono text-muted-foreground tabular-nums">
          <td className="py-1 pr-3 font-sans" colSpan={3}>
            Brier {num(curve.brier)} · ECE {num(curve.ece)} · n={curve.n}
            {curve.outcome ? ` · outcome: ${curve.outcome.replaceAll("_", " ")}` : ""}
          </td>
          <td colSpan={2} />
        </tr>
      </tbody>
    </table>
  );
}
