import { LoadRampChart } from "@/components/evals/load-chart";
import type { LoadEndpoint, LoadReport, LoadStage } from "@/lib/evals";
import { pct } from "@/lib/evals";

/**
 * The load-test section: what `python -m evals.load.run` measured against a
 * running API — 50 concurrent simulated clinicians, then a ramp until the
 * error rate or the p95 gave out. The machine and backend are printed next
 * to the numbers because they are part of the result.
 */
export function LoadMethodology({ report }: { report: LoadReport | null }) {
  return (
    <section className="space-y-4" aria-labelledby="load-heading" id="load">
      <div>
        <h2 id="load-heading" className="text-lg font-semibold tracking-tight">
          Load — 50 concurrent users, then a ramp to the breaking point
        </h2>
        <p className="mt-1 text-sm leading-6 text-muted-foreground">
          A Locust script (<code className="font-mono text-xs">evals/load/locustfile.py</code>) simulates clinicians
          asking the golden set&rsquo;s questions through the same streaming endpoint the product uses, reading each
          stream to its <code className="font-mono text-xs">result</code> event. Answers the semantic cache served are
          counted apart from answers the pipeline produced, so a cache cannot flatter the pipeline&rsquo;s numbers.
          The ramp holds each user count for a fixed window and stops at the first stage whose error rate or p95
          crosses the limit.
        </p>
      </div>

      {!report ? (
        <p className="rounded-md border border-dashed p-4 text-sm text-muted-foreground" data-testid="load-missing">
          The load test has not been run against this deployment — run{" "}
          <code className="text-xs">python -m evals.load.run</code> with the API up. Nothing is shown in its place.
        </p>
      ) : (
        <div className="space-y-4" data-testid="load-numbers">
          <p className="text-xs text-muted-foreground">
            Measured {new Date(report.generated_at).toUTCString()} against {report.target} · backend{" "}
            <code className="font-mono">{report.backend.ai_backend}</code> ({report.backend.reasoner} /{" "}
            {report.backend.embedder} / {report.backend.reranker}) · {report.machine.platform},{" "}
            {report.machine.cpu_count ?? "?"} CPUs · {report.questions} questions.
          </p>

          <StageTable label={`Sustained · ${report.sustained.users} users for ${report.sustained.seconds}s`} stage={report.sustained} />

          <div className="space-y-2">
            <h3 className="text-base font-semibold">Ramp</h3>
            {report.ramp.length > 0 ? (
              <>
                <LoadRampChart stages={report.ramp} p95LimitMs={report.criteria.p95_limit_ms} />
                <div className="overflow-x-auto">
                  <table className="w-full text-xs">
                    <thead className="text-muted-foreground">
                      <tr className="text-left">
                        <th className="py-1 font-medium">Users</th>
                        <th className="py-1 text-right font-medium">Requests</th>
                        <th className="py-1 text-right font-medium">Error rate</th>
                        <th className="py-1 text-right font-medium">Pipeline p50 / p95 / p99</th>
                        <th className="py-1 text-right font-medium">Cache hits</th>
                        <th className="py-1 pr-4 text-right font-medium">req/s</th>
                        <th className="py-1 font-medium">Outcome</th>
                      </tr>
                    </thead>
                    <tbody className="font-mono tabular-nums">
                      {report.ramp.map((s) => {
                        const ask = s.by_endpoint["ask (pipeline)"];
                        return (
                          <tr key={s.users} className="border-t">
                            <td className="py-1">{s.users}</td>
                            <td className="py-1 text-right">{s.total.requests}</td>
                            <td className="py-1 text-right">{pct(s.total.error_rate, 1)}</td>
                            <td className="py-1 text-right">{ask ? `${ms(ask.p50_ms)} / ${ms(ask.p95_ms)} / ${ms(ask.p99_ms)}` : "—"}</td>
                            <td className="py-1 text-right">{pct(s.cache_hit_rate)}</td>
                            <td className="py-1 pr-4 text-right">{s.total.rps.toFixed(1)}</td>
                            <td className={`py-1 font-sans ${s.broke ? "text-destructive" : "text-muted-foreground"}`}>{s.broke ?? "held"}</td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              </>
            ) : (
              <p className="text-sm text-muted-foreground">The ramp was skipped in this run.</p>
            )}
            <p className="text-sm leading-6">
              {report.breaking_point ? (
                <>
                  <strong>Breaking point:</strong> {report.breaking_point.users} users — {report.breaking_point.reason}.{" "}
                  The last stage that held was {report.capacity_users ?? "none"} users.
                </>
              ) : (
                <>
                  <strong>No breaking point reached</strong> within the ramp ({report.ramp.map((s) => s.users).join(" → ")} users): the
                  criteria were an error rate above {pct(report.criteria.max_error_rate)} or a p95 above{" "}
                  {ms(report.criteria.p95_limit_ms)}. The top of the ramp, {report.capacity_users ?? "—"} users, is a floor on
                  capacity, not a measurement of it.
                </>
              )}
            </p>
          </div>

          <ul className="list-disc space-y-1 pl-5 text-xs text-muted-foreground">
            {report.notes.map((n) => (
              <li key={n}>{n}</li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}

function ms(v: number | null | undefined): string {
  if (v === null || v === undefined) return "—";
  return v >= 1000 ? `${(v / 1000).toFixed(2)} s` : `${Math.round(v)} ms`;
}

function StageTable({ label, stage }: { label: string; stage: LoadStage }) {
  const rows: [string, LoadEndpoint | undefined][] = [
    ["ask (pipeline)", stage.by_endpoint["ask (pipeline)"]],
    ["ask (cache)", stage.by_endpoint["ask (cache)"]],
    ["ask (blocked)", stage.by_endpoint["ask (blocked)"]],
    ["ask (failed)", stage.by_endpoint["ask (failed)"]],
    ["history", stage.by_endpoint["history"]],
  ];
  return (
    <div className="space-y-2" data-testid="load-sustained">
      <h3 className="text-base font-semibold">{label}</h3>
      <dl className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Stat k="Requests" v={String(stage.total.requests)} />
        <Stat k="Error rate" v={pct(stage.total.error_rate, 1)} />
        <Stat k="Throughput" v={`${stage.total.rps.toFixed(1)} req/s`} />
        <Stat k="Cache hit rate" v={pct(stage.cache_hit_rate)} />
        <Stat k="First event p50 / p95" v={`${ms(stage.first_event.p50_ms)} / ${ms(stage.first_event.p95_ms)}`} />
        <Stat k="All requests p50 / p95 / p99" v={`${ms(stage.total.p50_ms)} / ${ms(stage.total.p95_ms)} / ${ms(stage.total.p99_ms)}`} />
      </dl>
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead className="text-muted-foreground">
            <tr className="text-left">
              <th className="py-1 font-medium">Request</th>
              <th className="py-1 text-right font-medium">n</th>
              <th className="py-1 text-right font-medium">Failed</th>
              <th className="py-1 text-right font-medium">p50</th>
              <th className="py-1 text-right font-medium">p95</th>
              <th className="py-1 text-right font-medium">p99</th>
              <th className="py-1 text-right font-medium">max</th>
            </tr>
          </thead>
          <tbody className="font-mono tabular-nums">
            {rows
              .filter((r): r is [string, LoadEndpoint] => r[1] !== undefined)
              .map(([name, e]) => (
                <tr key={name} className="border-t">
                  <td className="py-1 font-sans">{name}</td>
                  <td className="py-1 text-right">{e.requests}</td>
                  <td className="py-1 text-right">{e.failures}</td>
                  <td className="py-1 text-right">{ms(e.p50_ms)}</td>
                  <td className="py-1 text-right">{ms(e.p95_ms)}</td>
                  <td className="py-1 text-right">{ms(e.p99_ms)}</td>
                  <td className="py-1 text-right">{ms(e.max_ms)}</td>
                </tr>
              ))}
          </tbody>
        </table>
      </div>
      {stage.failures.length > 0 && (
        <ul className="list-disc pl-5 text-xs text-destructive">
          {stage.failures.map((f) => (
            <li key={`${f.name}-${f.error}`}>
              {f.name}: {f.error} ×{f.count}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function Stat({ k, v }: { k: string; v: string }) {
  return (
    <div className="rounded-md border px-3 py-2">
      <dt className="text-xs text-muted-foreground">{k}</dt>
      <dd className="font-mono text-sm tabular-nums">{v}</dd>
    </div>
  );
}
