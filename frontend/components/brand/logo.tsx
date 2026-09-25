import { cn } from "@/lib/utils";

/**
 * The brand mark: a white medical cross on an indigo-to-violet tile.
 *
 * Not a red cross on white — that is a protected emblem under the Geneva
 * Conventions, not a generic medical sign. The gradient is a CSS background,
 * not an SVG <linearGradient>, so any number of marks can share a page
 * without id collisions.
 */
export function LogoMark({ className }: { className?: string }) {
  return (
    <span
      aria-hidden
      className={cn(
        "grid size-7 shrink-0 place-items-center rounded-[28%] bg-[linear-gradient(135deg,#6366f1_0%,#7c3aed_100%)] shadow-[inset_0_1px_0_rgb(255_255_255/0.35),0_6px_14px_-6px_rgb(99_102_241/0.8)]",
        className,
      )}
    >
      <svg viewBox="0 0 24 24" className="size-[58%]" fill="none">
        <path
          d="M9.6 3.5h4.8a1 1 0 0 1 1 1v4.1h4.1a1 1 0 0 1 1 1v4.8a1 1 0 0 1-1 1h-4.1v4.1a1 1 0 0 1-1 1H9.6a1 1 0 0 1-1-1v-4.1H4.5a1 1 0 0 1-1-1V9.6a1 1 0 0 1 1-1h4.1V4.5a1 1 0 0 1 1-1Z"
          fill="white"
        />
      </svg>
    </span>
  );
}

/** The mark with the wordmark beside it. */
export function Logo({ className, markClassName }: { className?: string; markClassName?: string }) {
  return (
    <span className={cn("inline-flex items-center gap-2.5", className)}>
      <LogoMark className={markClassName} />
      <span className="font-semibold tracking-tight">ClinicalContext</span>
    </span>
  );
}
