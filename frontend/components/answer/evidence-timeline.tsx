"use client";

import { useMemo, useState } from "react";
import {
  CartesianGrid,
  ReferenceArea,
  ReferenceLine,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip,
  XAxis,
  YAxis,
  ZAxis,
} from "recharts";

import { GradeBadge, StanceDot } from "@/components/clinical/badges";
import { stanceByMarker, type Citation, type Contradiction, type EvidenceGrade, type Stance } from "@/lib/domain";
import { humanStudyType, yearOf } from "@/lib/text";
import { cn } from "@/lib/utils";

interface Point {
  marker: number;
  /** Lane plus a small vertical nudge when sources share a date. */
  y: number;
  /** Fractional year (month-precise), nudged apart when two sources would overlap. */
  x: number;
  year: number;
  /** Row on the chart: supports on top, opposes at the bottom, neutral between. */
  lane: number;
  size: number;
  stance: Stance;
  grade: EvidenceGrade | null;
  title: string;
  passage: string;
  studyType: string;
}

const LANE: Record<Stance, number> = { supports: 2, neutral: 1, opposes: 0 };
const LANE_LABEL = ["Opposes", "Neutral", "Supports"];
const SIZE: Record<EvidenceGrade | "none", number> = { A: 420, B: 300, C: 190, D: 120, none: 90 };

/**
 * Spec #15: cited sources by publication year, coloured by stance and sized
 * by evidence grade. When the dominant stance flips over time, the flip is
 * drawn as a shaded band with a label — it should be impossible to miss.
 */
export function EvidenceTimeline({
  citations,
  contradiction,
  activeMarker,
  onCite,
  className,
}: {
  citations: Citation[];
  contradiction: Contradiction;
  activeMarker?: number | null;
  onCite: (marker: number) => void;
  className?: string;
}) {
  const [hover, setHover] = useState<Point | null>(null);

  const { points, flip, domain, span } = useMemo(() => {
    const stances = stanceByMarker(contradiction, citations);
    const pts: Point[] = citations
      .map((c) => {
        const year = yearOf(c.publication_date);
        if (year === null) return null;
        const stance = stances.get(c.marker) ?? "neutral";
        const month = Number(c.publication_date?.slice(5, 7)) || 7;
        return {
          marker: c.marker,
          x: year + (month - 1) / 12,
          year,
          lane: LANE[stance],
          y: LANE[stance],
          size: SIZE[c.evidence_grade ?? "none"],
          stance,
          grade: c.evidence_grade,
          title: c.title ?? "Untitled source",
          passage: c.passage,
          studyType: humanStudyType(c.study_type),
        };
      })
      .filter((p): p is Point => p !== null)
      .sort((a, b) => a.x - b.x);
    // The x-axis runs over the sources' own dates with a margin, so five
    // papers from one year spread across the chart instead of sitting in a
    // two-year window as one blob.
    const xs = pts.map((p) => p.x);
    const lo = xs.length ? Math.min(...xs) : new Date().getFullYear() - 5;
    const hi = xs.length ? Math.max(...xs) : new Date().getFullYear();
    const pad = Math.max(0.5, (hi - lo) * 0.12);
    const width = hi - lo + 2 * pad;
    // Sources closer than a dot's width in one lane would still overlap (the
    // screenshot showed five sources as two): fan each cluster out, sideways
    // and up/down within the lane.
    const near = width * 0.07;
    let cluster: Point[] = [];
    const fan = (group: Point[]) => {
      group.forEach((p, i) => {
        const offset = i - (group.length - 1) / 2;
        p.x += offset * near * 0.8;
        p.y = p.lane + (group.length > 1 ? (i % 2 === 0 ? 0.18 : -0.18) : 0);
      });
    };
    for (const lane of [0, 1, 2]) {
      cluster = [];
      for (const p of pts.filter((q) => q.lane === lane)) {
        const last = cluster[cluster.length - 1];
        if (last && p.x - last.x > near) {
          fan(cluster);
          cluster = [];
        }
        cluster.push(p);
      }
      fan(cluster);
    }

    // A flip: the dominant non-neutral stance changes between two years.
    let flipRange: { from: number; to: number } | null = null;
    const byYear = new Map<number, Stance>();
    for (const p of pts) {
      if (p.stance === "neutral") continue;
      byYear.set(p.year, p.stance); // later marker wins within a year
    }
    const years = [...byYear.keys()].sort((a, b) => a - b);
    for (let i = 1; i < years.length; i += 1) {
      const prev = years[i - 1];
      const curr = years[i];
      if (prev !== undefined && curr !== undefined && byYear.get(prev) !== byYear.get(curr)) {
        flipRange = { from: prev, to: curr };
        break;
      }
    }
    return {
      points: pts,
      flip: flipRange,
      domain: [lo - pad, hi + pad] as [number, number],
      span: Math.max(...pts.map((q) => q.year), 0) - Math.min(...pts.map((q) => q.year), 9999),
    };
  }, [citations, contradiction]);

  if (points.length < 2) return null;
  const ticks: number[] = [];
  for (let y = Math.ceil(domain[0]); y <= Math.floor(domain[1]); y += 1) ticks.push(y);
  const step = Math.ceil(ticks.length / 8);
  const shownTicks = ticks.filter((_, i) => i % step === 0);

  return (
    <section className={cn("rounded-lg border bg-card", className)} aria-labelledby="timeline-heading">
      <header className="flex flex-wrap items-center justify-between gap-2 border-b px-4 py-2.5">
        <h2 id="timeline-heading" className="text-sm font-medium">
          Evidence timeline
          <span className="ml-2 font-mono text-xs text-muted-foreground">
            {points.length} sources · {span === 0 ? `all ${points[0]?.year ?? ""}` : `${span} yr span`}
          </span>
        </h2>
        <div className="flex items-center gap-3">
          <StanceDot stance="supports" />
          <StanceDot stance="opposes" />
          <StanceDot stance="neutral" />
          <span className="text-xs text-muted-foreground">· size = evidence grade</span>
        </div>
      </header>

      {flip && (
        <p className="border-b border-conflict/40 bg-conflict-bg/60 px-4 py-2 text-sm text-conflict-fg" role="note">
          <span className="font-semibold">Recommendation flipped</span> between {flip.from} and {flip.to}: the
          dominant stance in the cited evidence changed.
        </p>
      )}

      <div className="h-56 w-full px-2 pt-2" data-testid="evidence-timeline">
        <ResponsiveContainer width="100%" height="100%">
          <ScatterChart margin={{ top: 12, right: 24, bottom: 8, left: 8 }}>
            <CartesianGrid strokeDasharray="2 4" stroke="var(--border)" />
            <XAxis
              type="number"
              dataKey="x"
              domain={domain}
              ticks={shownTicks}
              allowDecimals={false}
              tickFormatter={(v: number) => String(Math.round(v))}
              tick={{ fontSize: 11, fill: "var(--muted-foreground)" }}
              axisLine={{ stroke: "var(--border)" }}
              tickLine={false}
              name="Year"
            />
            <YAxis
              type="number"
              dataKey="y"
              domain={[-0.5, 2.5]}
              ticks={[0, 1, 2]}
              tickFormatter={(v: number) => LANE_LABEL[v] ?? ""}
              tick={{ fontSize: 11, fill: "var(--muted-foreground)" }}
              axisLine={false}
              tickLine={false}
              width={64}
              name="Stance"
            />
            <ZAxis type="number" dataKey="size" range={[80, 460]} />
            {flip && (
              <>
                <ReferenceArea
                  x1={flip.from}
                  x2={flip.to}
                  y1={-0.5}
                  y2={2.5}
                  fill="var(--conflict)"
                  fillOpacity={0.12}
                  stroke="var(--conflict)"
                  strokeOpacity={0.5}
                  strokeDasharray="4 4"
                />
                <ReferenceLine
                  x={(flip.from + flip.to) / 2}
                  stroke="var(--conflict)"
                  strokeWidth={1.5}
                  label={{ value: "flip", position: "top", fontSize: 11, fill: "var(--conflict-fg)" }}
                />
              </>
            )}
            <Tooltip
              cursor={false}
              content={({ active, payload }) => {
                const p = active && payload && payload[0] ? (payload[0].payload as Point) : null;
                if (!p) return null;
                return (
                  <div className="max-w-xs rounded-md border bg-popover p-3 text-xs shadow-md">
                    <div className="mb-1 flex items-center gap-2">
                      <span className="cite-chip">{p.marker}</span>
                      <span className="font-mono text-muted-foreground">{p.year}</span>
                      <GradeBadge grade={p.grade} />
                    </div>
                    <p className="font-medium leading-5">{p.title}</p>
                    <p className="text-muted-foreground">{p.studyType} · {p.stance}</p>
                    <p className="mt-1 line-clamp-3 leading-5 text-foreground/80">{p.passage}</p>
                    <p className="mt-1 text-[10px] text-muted-foreground">Click to open the source</p>
                  </div>
                );
              }}
            />
            <Scatter
              data={points}
              onClick={(entry) => {
                const p = entry as unknown as Point;
                if (typeof p.marker === "number") onCite(p.marker);
              }}
              onMouseEnter={(entry) => setHover(entry as unknown as Point)}
              onMouseLeave={() => setHover(null)}
              cursor="pointer"
              shape={(props: unknown) => {
                const { cx, cy, payload } = props as { cx: number; cy: number; payload: Point };
                // Big enough to read the marker number inside, bigger for stronger evidence.
                const r = Math.max(9, Math.sqrt(payload.size / Math.PI) * 0.9);
                const active = activeMarker === payload.marker || hover?.marker === payload.marker;
                const fill =
                  payload.stance === "supports"
                    ? "var(--stance-supports)"
                    : payload.stance === "opposes"
                      ? "var(--stance-opposes)"
                      : "var(--stance-neutral)";
                return (
                  <g role="button" aria-label={`Source ${payload.marker}, ${payload.year}, ${payload.stance}`}>
                    <circle cx={cx} cy={cy} r={r + (active ? 3 : 0)} fill={fill} fillOpacity={active ? 1 : 0.85} stroke="var(--background)" strokeWidth={2} />
                    <text x={cx} y={cy + 3.5} textAnchor="middle" fontSize={10} fontFamily="var(--font-geist-mono)" fill="var(--background)" fontWeight={700}>
                      {payload.marker}
                    </text>
                  </g>
                );
              }}
            />
          </ScatterChart>
        </ResponsiveContainer>
      </div>
    </section>
  );
}
