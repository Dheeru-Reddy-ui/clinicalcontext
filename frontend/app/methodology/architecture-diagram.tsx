/**
 * The request path, as one inline SVG: what a click in the browser goes
 * through before an answer comes back, and where the observability signals
 * (Phase 13) attach. Colours are the page's own tokens so the diagram reads
 * in both themes; every box is a real module in the repository.
 */

type Box = { x: number; y: number; w: number; h: number; title: string; sub?: string; tone?: "accent" | "muted" };

const BOXES: Box[] = [
  { x: 10, y: 40, w: 130, h: 56, title: "Browser", sub: "Next.js · SSE + WebSocket" },
  { x: 180, y: 40, w: 130, h: 56, title: "API", sub: "FastAPI · auth · rate limit" },
  { x: 350, y: 40, w: 130, h: 56, title: "Guardrails", sub: "PHI · scope · red flags" },
  { x: 520, y: 40, w: 150, h: 56, title: "Semantic cache", sub: "Redis · per tenant" },
  { x: 180, y: 140, w: 490, h: 118, title: "LangGraph", tone: "accent" },
  { x: 10, y: 300, w: 200, h: 56, title: "Postgres + pgvector", sub: "dense · BM25 · RLS per tenant" },
  { x: 250, y: 300, w: 200, h: 56, title: "Embed · rerank", sub: "Cohere, or local stand-ins" },
  { x: 490, y: 300, w: 180, h: 56, title: "Generate · judge", sub: "Anthropic, or extractive" },
  { x: 10, y: 140, w: 130, h: 118, title: "Voice", sub: "STT → same graph → TTS", tone: "muted" },
];

const NODES = ["classify", "decompose", "retrieve", "grade", "rewrite", "contradiction", "generate", "verify", "assess"];

const SIGNALS = [
  { label: "OpenTelemetry trace", note: "one trace per query, tenant_id · query_id · request_id on every span" },
  { label: "Sentry", note: "errors with release + request_id, both tiers" },
  { label: "LangSmith", note: "every graph run, prompt versions + eval scores" },
  { label: "Cost ledger", note: "every provider call: units, price, cache savings" },
];

export function ArchitectureDiagram() {
  return (
    <figure className="space-y-2" data-testid="architecture-diagram">
      <svg
        viewBox="0 0 690 470"
        role="img"
        aria-labelledby="arch-title arch-desc"
        className="w-full rounded-md border bg-card text-foreground"
        fontFamily="ui-sans-serif, system-ui, sans-serif"
      >
        <title id="arch-title">ClinicalContext request path</title>
        <desc id="arch-desc">
          Browser to API to guardrails and semantic cache; the LangGraph pipeline of classify, decompose, retrieve,
          grade, rewrite, contradiction, generate, verify, assess; retrieval against Postgres with pgvector and BM25;
          embedding, rerank and generation providers; voice enters the same graph. Traces, errors, graph runs and
          costs are recorded along the way.
        </desc>
        <defs>
          <marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
            <path d="M 0 0 L 10 5 L 0 10 z" fill="var(--muted-foreground)" />
          </marker>
        </defs>

        {/* flow arrows */}
        {[
          [140, 68, 180, 68],
          [310, 68, 350, 68],
          [480, 68, 520, 68],
          [415, 96, 415, 140],
          [140, 199, 180, 199],
          [110, 258, 110, 300],
          [350, 258, 350, 300],
          [580, 258, 580, 300],
        ].map(([x1, y1, x2, y2], i) => (
          <line key={i} x1={x1} y1={y1} x2={x2} y2={y2} stroke="var(--muted-foreground)" strokeWidth="1.2" markerEnd="url(#arrow)" />
        ))}

        {BOXES.map((b) => (
          <g key={b.title}>
            <rect
              x={b.x}
              y={b.y}
              width={b.w}
              height={b.h}
              rx="6"
              fill={b.tone === "accent" ? "var(--accent)" : b.tone === "muted" ? "var(--muted)" : "var(--card)"}
              stroke="var(--border)"
              strokeWidth="1"
            />
            <text x={b.x + 10} y={b.y + 22} fontSize="12" fontWeight="600" fill="currentColor">
              {b.title}
            </text>
            {b.sub && (
              <text x={b.x + 10} y={b.y + 40} fontSize="10.5" fill="var(--muted-foreground)">
                {b.sub}
              </text>
            )}
          </g>
        ))}

        {/* graph nodes inside the LangGraph box */}
        {NODES.map((name, i) => {
          const col = i % 5;
          const row = Math.floor(i / 5);
          const x = 192 + col * 96;
          const y = 172 + row * 40;
          return (
            <g key={name}>
              <rect x={x} y={y} width="86" height="26" rx="4" fill="var(--card)" stroke="var(--border)" />
              <text x={x + 43} y={y + 17} fontSize="10.5" textAnchor="middle" fill="currentColor">
                {name}
              </text>
              {i < NODES.length - 1 && col < 4 && (
                <line x1={x + 86} y1={y + 13} x2={x + 96} y2={y + 13} stroke="var(--muted-foreground)" strokeWidth="1" />
              )}
            </g>
          );
        })}
        <text x={660} y={252} fontSize="10" textAnchor="end" fill="var(--muted-foreground)">
          grade fails → rewrite → retrieve again (bounded)
        </text>

        {/* observability rail */}
        <rect x="10" y="380" width="660" height="80" rx="6" fill="var(--muted)" stroke="var(--border)" strokeDasharray="3 3" />
        <text x="20" y="400" fontSize="11" fontWeight="600" fill="currentColor">
          Recorded on the way through
        </text>
        {SIGNALS.map((s, i) => (
          <g key={s.label} transform={`translate(${20 + i * 162}, 414)`}>
            <text fontSize="10.5" fontWeight="600" fill="currentColor">
              {s.label}
            </text>
            <foreignObject x="0" y="6" width="156" height="40">
              <p style={{ fontSize: 9.5, lineHeight: "12px", margin: 0, color: "var(--muted-foreground)" }}>{s.note}</p>
            </foreignObject>
          </g>
        ))}
      </svg>
      <figcaption className="text-xs text-muted-foreground">
        The request path. A query is one OpenTelemetry trace from the browser click to the provider calls; the same
        graph serves text and voice.
      </figcaption>
    </figure>
  );
}
