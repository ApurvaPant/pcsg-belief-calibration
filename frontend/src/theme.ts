// Colour per dimension. Keep the keys in sync with backend constants.DIMENSIONS.
// Brightened (Tailwind ~400) shades tuned for contrast on a dark canvas.
// Harmonised ~Tailwind-400 family: one consistent lightness/saturation band so
// the set reads as a palette, with hues spread for categorical distinctness.
export const DIMENSION_COLORS: Record<string, string> = {
  identity: "#eceef3",            // near-white — the core of "you"
  preference: "#7aa2ff",          // indigo-blue (accent family)
  interest: "#38c7e8",            // cyan
  goal: "#fb8a8a",                // coral
  skill: "#5cd39a",               // emerald
  project: "#f2c558",             // amber
  habit: "#b197fc",               // violet
  belief: "#f58cc0",              // pink
  relationship: "#34d6be",        // teal
  communication_style: "#9aa6b8", // slate
};

export const DIMENSION_ORDER = Object.keys(DIMENSION_COLORS);

export function colorFor(dimension: string): string {
  return DIMENSION_COLORS[dimension] ?? "#94a3b8";
}
