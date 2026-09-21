"use client";

import { useEffect, useRef } from "react";

import { cn } from "@/lib/utils";

/**
 * A live waveform: the user's microphone level on the left half, the
 * agent's playback level on the right. Drawn on a canvas from the RMS of
 * each 20 ms frame — no audio analysis on the render thread beyond that.
 */
export function Waveform({
  micLevel,
  agentLevel,
  active,
  className,
}: {
  micLevel: number;
  agentLevel: number;
  active: boolean;
  className?: string;
}) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const history = useRef<{ mic: number[]; agent: number[] }>({ mic: [], agent: [] });

  useEffect(() => {
    const h = history.current;
    h.mic.push(micLevel);
    h.agent.push(agentLevel);
    if (h.mic.length > 96) h.mic.shift();
    if (h.agent.length > 96) h.agent.shift();
    const el = canvas.current;
    if (!el) return;
    const ctx = el.getContext("2d");
    if (!ctx) return;
    const dpr = window.devicePixelRatio || 1;
    const width = el.clientWidth;
    const height = el.clientHeight;
    if (el.width !== width * dpr || el.height !== height * dpr) {
      el.width = width * dpr;
      el.height = height * dpr;
    }
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, width, height);
    const styles = getComputedStyle(el);
    const draw = (values: number[], color: string, offset: number, span: number) => {
      const bar = span / 96;
      values.forEach((value, i) => {
        const amplitude = Math.min(1, value * 6);
        const barHeight = Math.max(2, amplitude * height * 0.9);
        ctx.fillStyle = color;
        ctx.globalAlpha = active ? 0.9 : 0.35;
        ctx.fillRect(offset + i * bar, (height - barHeight) / 2, Math.max(1, bar - 1), barHeight);
      });
    };
    draw(h.mic, styles.getPropertyValue("--primary") || "#4f46e5", 0, width / 2 - 8);
    draw(h.agent, styles.getPropertyValue("--citation") || "#0ea5e9", width / 2 + 8, width / 2 - 8);
    ctx.globalAlpha = 1;
  }, [micLevel, agentLevel, active]);

  return (
    <div className={cn("relative h-16 w-full", className)} aria-hidden>
      <canvas ref={canvas} className="h-full w-full" />
      <span className="absolute left-0 top-0 text-[10px] uppercase tracking-wide text-muted-foreground">
        you
      </span>
      <span className="absolute right-0 top-0 text-[10px] uppercase tracking-wide text-muted-foreground">
        agent
      </span>
    </div>
  );
}
