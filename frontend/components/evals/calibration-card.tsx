"use client";

import { Gauge } from "lucide-react";

import { CalibrationCurveChart, CalibrationTable } from "@/components/evals/calibration-curve";
import { Skeleton } from "@/components/ui/skeleton";
import { usePublicEval } from "@/hooks/use-api";
import type { CalibrationReport } from "@/lib/evals";

/**
 * The dashboard's calibration card (Phase 12): stated confidence against
 * outcome on the golden set and against thumbs feedback in production, and
 * whether the last calibration run recommended retuning the thresholds.
 * Read from evals/results/calibration.json — the output of
 * `python -m evals.golden.calibrate` — never typed in.
 */
export function CalibrationCard() {
  const report = usePublicEval<CalibrationReport>("calibration");
  const data = report.data;
  const curves = data ? Object.entries(data.curves) : [];
  return (
    <section className="rounded-lg border bg-card" aria-labelledby="calibration-heading">
      <header className="flex flex-wrap items-center justify-between gap-2 border-b px-4 py-2.5">
        <h2 id="calibration-heading" className="flex items-center gap-2 text-sm font-medium">
          <Gauge className="size-4" aria-hidden /> Confidence calibration
        </h2>
        {data && (
          <span className={`font-mono text-xs ${data.recommendation.retune ? "text-destructive" : "text-muted-foreground"}`}>
            {data.recommendation.retune ? "retune recommended" : "within drift threshold"}
          </span>
        )}
      </header>
      {report.isPending ? (
        <div className="space-y-2 p-4">
          <Skeleton className="h-6 w-full" />
          <Skeleton className="h-6 w-full" />
        </div>
      ) : !data ? (
        <p className="p-4 text-sm text-muted-foreground">
          No calibration report yet — run <code className="text-xs">python -m evals.golden.calibrate</code>.
        </p>
      ) : (
        <div className="grid gap-4 p-4 md:grid-cols-2">
          {curves.map(([name, curve]) => (
            <div key={name} className="space-y-2">
              <h3 className="text-xs font-medium">
                {name === "golden" ? "Golden set" : "Production (thumbs feedback)"}
                <span className="font-normal text-muted-foreground"> · outcome: {curve.outcome?.replaceAll("_", " ") ?? "—"}</span>
              </h3>
              <CalibrationCurveChart curve={curve} height={160} />
              <CalibrationTable curve={curve} />
            </div>
          ))}
          {data.recommendation.reasons.length > 0 && (
            <ul className="list-disc space-y-1 pl-5 text-xs text-muted-foreground md:col-span-2">
              {data.recommendation.reasons.map((r) => (
                <li key={r}>{r}</li>
              ))}
            </ul>
          )}
        </div>
      )}
    </section>
  );
}
