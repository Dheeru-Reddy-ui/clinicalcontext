"use client";

import dynamic from "next/dynamic";
import { useCallback, useEffect, useRef, useState } from "react";

import { HeroStill } from "@/components/landing/hero-still";
import { cn } from "@/lib/utils";

/**
 * The 3D layer of the hero, and everything that decides whether it runs.
 *
 * - three.js is loaded only here, after the page is interactive: the
 *   headline, the buttons and the cards are server-rendered and never wait
 *   on WebGL.
 * - It only runs on a GPU. A context that fails `failIfMajorPerformanceCaveat`
 *   is software rendering (old machines, locked-down browsers, headless test
 *   runs), where a 3D scene would cost seconds of CPU; those get a still
 *   illustration of the same composition instead.
 * - It stops rendering when the hero scrolls out of view, and renders a
 *   single still frame for people who ask their system for reduced motion.
 *
 * It also publishes the pointer position (--mx, --my) and where the
 * centrepiece sits (--anchor-y) as CSS variables on the hero, for the glass
 * cards' parallax and the ECG trace behind the cross.
 */

const HeroScene = dynamic(() => import("@/components/landing/hero-scene"), { ssr: false });

type Mode = "pending" | "3d" | "still";

function hardwareWebGL(): boolean {
  try {
    const canvas = document.createElement("canvas");
    const options: WebGLContextAttributes = { failIfMajorPerformanceCaveat: true };
    const gl: WebGLRenderingContext | WebGL2RenderingContext | null =
      canvas.getContext("webgl2", options) ?? canvas.getContext("webgl", options);
    if (!gl) return false;
    gl.getExtension("WEBGL_lose_context")?.loseContext();
    return true;
  } catch {
    return false;
  }
}

function usePrefersReducedMotion(): boolean {
  const [reduced, setReduced] = useState(false);
  useEffect(() => {
    const query = window.matchMedia("(prefers-reduced-motion: reduce)");
    const update = () => setReduced(query.matches);
    update();
    query.addEventListener("change", update);
    return () => query.removeEventListener("change", update);
  }, []);
  return reduced;
}

export function Hero3D() {
  const layer = useRef<HTMLDivElement>(null);
  const hero = useRef<HTMLElement | null>(null);
  const [mode, setMode] = useState<Mode>("pending");
  const [anchor, setAnchor] = useState({ x: 0.5, y: 0.72 });
  const [active, setActive] = useState(true);
  const [ready, setReady] = useState(false);
  const reducedMotion = usePrefersReducedMotion();

  useEffect(() => {
    hero.current = layer.current?.closest<HTMLElement>("[data-hero]") ?? null;
    setMode(hardwareWebGL() ? "3d" : "still");
  }, []);

  // Where the stage is, so the centrepiece sits in it on every screen size.
  useEffect(() => {
    const root = hero.current;
    const box = layer.current;
    const stage = root?.querySelector<HTMLElement>("[data-hero-anchor]");
    if (!root || !box || !stage) return;
    const measure = () => {
      const b = box.getBoundingClientRect();
      const s = stage.getBoundingClientRect();
      if (!b.width || !b.height) return;
      const next = {
        x: (s.left + s.width / 2 - b.left) / b.width,
        y: (s.top + s.height / 2 - b.top) / b.height,
      };
      root.style.setProperty("--anchor-y", `${(next.y * 100).toFixed(2)}%`);
      setAnchor((prev) =>
        Math.abs(prev.x - next.x) < 0.002 && Math.abs(prev.y - next.y) < 0.002 ? prev : next,
      );
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(root);
    observer.observe(stage);
    return () => observer.disconnect();
  }, [mode]);

  // Render only while the hero is on screen.
  useEffect(() => {
    const root = hero.current;
    if (!root) return;
    const observer = new IntersectionObserver(([entry]) => setActive(Boolean(entry?.isIntersecting)), {
      rootMargin: "120px 0px",
    });
    observer.observe(root);
    return () => observer.disconnect();
  }, [mode]);

  // The pointer, for the cards' parallax: -1..1 across the window.
  useEffect(() => {
    const root = hero.current;
    if (!root || reducedMotion) return;
    let frame = 0;
    const onMove = (e: PointerEvent) => {
      if (e.pointerType === "touch") return;
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => {
        root.style.setProperty("--mx", ((e.clientX / window.innerWidth) * 2 - 1).toFixed(3));
        root.style.setProperty("--my", ((e.clientY / window.innerHeight) * 2 - 1).toFixed(3));
      });
    };
    window.addEventListener("pointermove", onMove, { passive: true });
    return () => {
      cancelAnimationFrame(frame);
      window.removeEventListener("pointermove", onMove);
    };
  }, [reducedMotion, mode]);

  const onReady = useCallback(() => setReady(true), []);

  return (
    <div ref={layer} className="pointer-events-none absolute inset-0 z-[1]" aria-hidden data-hero-3d={mode}>
      {mode === "still" && <HeroStill anchor={anchor} />}
      {mode === "3d" && (
        <div className={cn("absolute inset-0 transition-opacity duration-1000 ease-out", ready ? "opacity-100" : "opacity-0")}>
          <HeroScene
            anchor={anchor}
            animate={!reducedMotion}
            active={active}
            scrollRoot={hero}
            onReady={onReady}
          />
        </div>
      )}
    </div>
  );
}
