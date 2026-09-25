/**
 * Text size, per device: larger type for people who read on a small phone
 * or with tired eyes. Everything in the app is sized in rem, so one root
 * font size scales it all.
 *
 * Stored in a cookie rather than on the server: it is a property of the
 * screen, not of the person (large on the phone, normal on the laptop), and
 * the root layout reads it before rendering, so the page never flashes at
 * the wrong size.
 */

export const TEXT_SIZE_COOKIE = "cc-text-size";

export const TEXT_SIZES = ["default", "large", "larger"] as const;
export type TextSize = (typeof TEXT_SIZES)[number];

export const TEXT_SIZE_LABELS: Record<TextSize, string> = {
  default: "Default",
  large: "Large",
  larger: "Larger",
};

export function parseTextSize(value: string | undefined | null): TextSize {
  return TEXT_SIZES.includes(value as TextSize) ? (value as TextSize) : "default";
}

/** Apply now and remember for a year on this device. */
export function setTextSize(size: TextSize): void {
  document.documentElement.dataset.textSize = size;
  document.cookie = `${TEXT_SIZE_COOKIE}=${size}; path=/; max-age=${60 * 60 * 24 * 365}; samesite=lax`;
}

export function currentTextSize(): TextSize {
  if (typeof document === "undefined") return "default";
  return parseTextSize(document.documentElement.dataset.textSize);
}
