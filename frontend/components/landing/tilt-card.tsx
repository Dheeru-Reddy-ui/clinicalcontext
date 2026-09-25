"use client";

import { useRef, type PointerEvent, type ReactNode } from "react";

import { cn } from "@/lib/utils";

/**
 * A card that tilts toward the pointer and catches a highlight where the
 * pointer is, like a physical card under a lamp. Mouse only — touch
 * scrolling must never tilt anything — and nothing at all under reduced
 * motion (the .tilt-card rule in globals.css).
 */
export function TiltCard({ className, children }: { className?: string; children: ReactNode }) {
  const ref = useRef<HTMLDivElement>(null);

  const onMove = (e: PointerEvent<HTMLDivElement>) => {
    const el = ref.current;
    if (!el || e.pointerType !== "mouse") return;
    const box = el.getBoundingClientRect();
    const px = (e.clientX - box.left) / box.width;
    const py = (e.clientY - box.top) / box.height;
    el.style.setProperty("--tilt-y", `${((px - 0.5) * 9).toFixed(2)}deg`);
    el.style.setProperty("--tilt-x", `${(-(py - 0.5) * 9).toFixed(2)}deg`);
    el.style.setProperty("--lift", "-6px");
    el.style.setProperty("--glare-x", `${(px * 100).toFixed(1)}%`);
    el.style.setProperty("--glare-y", `${(py * 100).toFixed(1)}%`);
    el.dataset.hover = "true";
  };

  const onLeave = () => {
    const el = ref.current;
    if (!el) return;
    for (const name of ["--tilt-x", "--tilt-y", "--lift"]) el.style.removeProperty(name);
    delete el.dataset.hover;
  };

  return (
    <div ref={ref} onPointerMove={onMove} onPointerLeave={onLeave} className={cn("group/tilt tilt-card relative", className)}>
      {children}
      <span
        aria-hidden
        className="pointer-events-none absolute inset-0 rounded-[inherit] opacity-0 transition-opacity duration-300 group-data-[hover=true]/tilt:opacity-100"
        style={{
          background:
            "radial-gradient(420px circle at var(--glare-x, 50%) var(--glare-y, 0%), rgb(255 255 255 / 0.55), transparent 45%)",
          mixBlendMode: "soft-light",
        }}
      />
    </div>
  );
}
