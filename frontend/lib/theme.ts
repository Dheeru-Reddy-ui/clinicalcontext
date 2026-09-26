/**
 * The looks ClinicalContext comes in, and how "match my device" works.
 *
 * Three looks that each differ from the others — Light, Dark (indigo-navy)
 * and Midnight (pure black, higher contrast) — plus next-themes' "system",
 * which is not a look of its own: it follows the device between Light and
 * Dark. Settings shows it as a switch, not a fourth choice, because on a
 * dark-mode device it would look exactly like Dark.
 */

export const THEME_LOOKS = ["light", "dark", "midnight"] as const;
export type ThemeLook = (typeof THEME_LOOKS)[number];

export const THEME_LABELS: Record<ThemeLook, string> = {
  light: "Light",
  dark: "Dark",
  midnight: "Midnight",
};

export const THEME_HINTS: Record<ThemeLook, string> = {
  light: "Bright and clear",
  dark: "Deep indigo-navy",
  midnight: "Pure black, high contrast",
};

/** The value next-themes stores when the look follows the device. */
export const MATCH_DEVICE = "system";

export function isThemeLook(value: string | undefined): value is ThemeLook {
  return value !== undefined && (THEME_LOOKS as readonly string[]).includes(value);
}

/** Whether a resolved theme is one of the dark looks. */
export function isDarkLook(value: string | undefined): boolean {
  return value === "dark" || value === "midnight";
}
