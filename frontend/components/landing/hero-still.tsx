"use client";

import { useId } from "react";

import { cn } from "@/lib/utils";

/**
 * The hero's centrepiece as a flat illustration, for devices where the 3D
 * scene is not worth running (no GPU). Same composition — pearl cross, ring
 * light, orbit of capsules and tablets — shaded with gradients.
 */

/** An SVG path for a plus sign with every corner rounded. */
function roundedCrossPath(cx: number, cy: number, arm: number, span: number, r: number): string {
  const a = arm / 2;
  const b = span / 2;
  const points: Array<[number, number]> = [
    [a, -b], [a, -a], [b, -a], [b, a], [a, a], [a, b],
    [-a, b], [-a, a], [-b, a], [-b, -a], [-a, -a], [-a, -b],
  ];
  const n = points.length;
  let d = "";
  for (let i = 0; i < n; i++) {
    const [px, py] = points[(i - 1 + n) % n] as [number, number];
    const [x, y] = points[i] as [number, number];
    const [nx, ny] = points[(i + 1) % n] as [number, number];
    const toPrev = [px - x, py - y];
    const toNext = [nx - x, ny - y];
    const lp = Math.hypot(toPrev[0] ?? 0, toPrev[1] ?? 0);
    const ln = Math.hypot(toNext[0] ?? 0, toNext[1] ?? 0);
    const sx = x + ((toPrev[0] ?? 0) / lp) * r;
    const sy = y + ((toPrev[1] ?? 0) / lp) * r;
    const ex = x + ((toNext[0] ?? 0) / ln) * r;
    const ey = y + ((toNext[1] ?? 0) / ln) * r;
    d += `${i === 0 ? "M" : "L"}${(cx + sx).toFixed(1)} ${(cy + sy).toFixed(1)} `;
    d += `Q${(cx + x).toFixed(1)} ${(cy + y).toFixed(1)} ${(cx + ex).toFixed(1)} ${(cy + ey).toFixed(1)} `;
  }
  return `${d}Z`;
}

function Capsule({
  x,
  y,
  angle,
  length,
  radius,
  capFill,
  bodyFill,
}: {
  x: number;
  y: number;
  angle: number;
  length: number;
  radius: number;
  capFill: string;
  bodyFill: string;
}) {
  const half = length / 2;
  return (
    <g transform={`translate(${x} ${y}) rotate(${angle})`}>
      <rect x={-half} y={-radius} width={half + radius} height={radius * 2} rx={radius} fill={bodyFill} />
      <rect x={-radius * 0.2} y={-radius * 1.04} width={half + radius * 0.2} height={radius * 2.08} rx={radius} fill={capFill} />
      <rect x={-half + radius * 0.5} y={-radius * 0.72} width={length - radius} height={radius * 0.34} rx={radius * 0.17} fill="white" opacity={0.55} />
    </g>
  );
}

export function HeroStill({ anchor, className }: { anchor: { x: number; y: number }; className?: string }) {
  const id = useId().replace(/:/g, "");
  const cross = roundedCrossPath(260, 210, 88, 250, 17);
  return (
    <svg
      viewBox="0 0 520 420"
      className={cn("absolute w-[min(560px,92vw)] -translate-x-1/2 -translate-y-1/2", className)}
      style={{ left: `${anchor.x * 100}%`, top: `${anchor.y * 100}%` }}
      data-testid="hero-still"
    >
      <defs>
        <radialGradient id={`${id}glow`}>
          <stop offset="0" stopColor="#818cf8" stopOpacity="0.55" />
          <stop offset="0.55" stopColor="#6366f1" stopOpacity="0.14" />
          <stop offset="1" stopColor="#6366f1" stopOpacity="0" />
        </radialGradient>
        <linearGradient id={`${id}pearl`} x1="0.15" y1="0" x2="0.85" y2="1">
          <stop offset="0" stopColor="#ffffff" />
          <stop offset="0.55" stopColor="#e0e7ff" />
          <stop offset="1" stopColor="#a5b4fc" />
        </linearGradient>
        <linearGradient id={`${id}gloss`} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="white" stopOpacity="0.9" />
          <stop offset="0.45" stopColor="white" stopOpacity="0" />
        </linearGradient>
        <linearGradient id={`${id}white`} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="#ffffff" />
          <stop offset="1" stopColor="#c7d2fe" />
        </linearGradient>
        <linearGradient id={`${id}indigo`} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="#a5b4fc" />
          <stop offset="1" stopColor="#4f46e5" />
        </linearGradient>
        <linearGradient id={`${id}teal`} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="#99f6e4" />
          <stop offset="1" stopColor="#0d9488" />
        </linearGradient>
        <linearGradient id={`${id}coral`} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="#fecdd3" />
          <stop offset="1" stopColor="#e11d48" />
        </linearGradient>
        <filter id={`${id}soft`} x="-20%" y="-20%" width="140%" height="140%">
          <feGaussianBlur stdDeviation="6" />
        </filter>
        <filter id={`${id}shadow`} x="-30%" y="-30%" width="160%" height="160%">
          <feDropShadow dx="0" dy="16" stdDeviation="18" floodColor="#1e1b4b" floodOpacity="0.7" />
        </filter>
      </defs>

      <circle cx="260" cy="210" r="210" fill={`url(#${id}glow)`} />
      <circle cx="260" cy="210" r="132" fill="none" stroke="#6366f1" strokeWidth="14" opacity="0.35" filter={`url(#${id}soft)`} />
      <circle cx="260" cy="210" r="132" fill="none" stroke="#c7d2fe" strokeWidth="2.5" />

      <ellipse cx="260" cy="222" rx="226" ry="62" fill="none" stroke="#a5b4fc" strokeOpacity="0.35" transform="rotate(-9 260 222)" />

      <Capsule x={420} y={112} angle={38} length={92} radius={21} capFill={`url(#${id}teal)`} bodyFill={`url(#${id}white)`} />
      <ellipse cx="104" cy="120" rx="30" ry="23" fill={`url(#${id}white)`} />
      <path d="M84 120 Q104 116 124 120" stroke="#a5b4fc" strokeWidth="1.5" fill="none" />

      <g filter={`url(#${id}shadow)`}>
        <path d={cross} fill={`url(#${id}pearl)`} stroke="white" strokeOpacity="0.8" strokeWidth="1.5" />
        <path d={cross} fill={`url(#${id}gloss)`} />
      </g>

      <Capsule x={96} y={302} angle={-28} length={104} radius={24} capFill={`url(#${id}indigo)`} bodyFill={`url(#${id}white)`} />
      <Capsule x={446} y={316} angle={-14} length={76} radius={17} capFill={`url(#${id}coral)`} bodyFill={`url(#${id}white)`} />
      <ellipse cx="360" cy="372" rx="24" ry="18" fill={`url(#${id}white)`} />
    </svg>
  );
}
