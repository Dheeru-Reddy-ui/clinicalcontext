"use client";

import { GradeBadge } from "@/components/clinical/badges";
import type { ComparisonTable as ComparisonTableData } from "@/lib/domain";
import { cn } from "@/lib/utils";

/**
 * Spec #16: the entity × outcome table. Sticky first column, per-cell
 * citation chips and grade badges, and "insufficient evidence" cells styled
 * as an honest statement — muted, not red, not an error.
 */
export function ComparisonTable({
  table,
  activeMarker,
  onCite,
  className,
}: {
  table: ComparisonTableData;
  activeMarker?: number | null;
  onCite: (marker: number) => void;
  className?: string;
}) {
  const cell = (entity: string, outcome: string) =>
    table.cells.find((c) => c.entity === entity && c.outcome === outcome);
  const insufficient = table.cells.filter((c) => !c.sufficient).length;

  return (
    <div className={cn("overflow-hidden rounded-lg border bg-card", className)}>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[40rem] border-collapse text-sm" data-testid="comparison-table">
          <caption className="sr-only">
            Comparison of {table.entities.join(", ")} across {table.outcomes.join(", ")}
          </caption>
          <thead>
            <tr className="bg-muted/60">
              <th
                scope="col"
                className="sticky left-0 z-10 w-44 border-b border-r bg-muted/60 px-3 py-2 text-left text-xs font-medium uppercase tracking-wide text-muted-foreground backdrop-blur"
              >
                Option
              </th>
              {table.outcomes.map((o) => (
                <th
                  key={o}
                  scope="col"
                  className="border-b px-3 py-2 text-left text-xs font-medium uppercase tracking-wide text-muted-foreground"
                >
                  {o}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {table.entities.map((entity) => (
              <tr key={entity} className="align-top">
                <th
                  scope="row"
                  className="sticky left-0 z-10 border-b border-r bg-card px-3 py-3 text-left font-medium backdrop-blur"
                >
                  {entity}
                </th>
                {table.outcomes.map((outcome) => {
                  const c = cell(entity, outcome);
                  if (!c || !c.sufficient) {
                    return (
                      <td
                        key={outcome}
                        className="border-b bg-muted/30 px-3 py-3 text-muted-foreground"
                        data-testid="insufficient-cell"
                      >
                        <span className="inline-flex items-center gap-1.5 text-xs italic">
                          Insufficient evidence
                        </span>
                        <p className="mt-1 text-[11px] not-italic text-muted-foreground/80">
                          No passage about {entity} addressed {outcome}.
                        </p>
                      </td>
                    );
                  }
                  return (
                    <td key={outcome} className="border-b px-3 py-3">
                      <p className="leading-6">{c.summary}</p>
                      <div className="mt-2 flex flex-wrap items-center gap-1.5">
                        {c.citations.map((m) => (
                          <button
                            key={m}
                            type="button"
                            className="cite-chip"
                            data-active={activeMarker === m}
                            onClick={() => onCite(m)}
                            aria-label={`Open source ${m}`}
                          >
                            {m}
                          </button>
                        ))}
                        <GradeBadge grade={c.evidence_grade} />
                      </div>
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {insufficient > 0 && (
        <p className="border-t px-3 py-2 text-xs text-muted-foreground">
          {insufficient} of {table.cells.length} cells had no evidence about that option and outcome.
          Those cells are left empty on purpose rather than filled with adjacent evidence.
        </p>
      )}
    </div>
  );
}
