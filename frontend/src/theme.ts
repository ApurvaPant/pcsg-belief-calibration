// Colour per dimension. Keep the keys in sync with backend constants.DIMENSIONS.
// Brightened (Tailwind ~400) shades tuned for contrast on a dark canvas.
export const DIMENSION_COLORS: Record<string, string> = {
  identity: "#f4f4f6",            // near-white — the core of "you"
  preference: "#60a5fa",          // blue
  interest: "#38bdf8",            // sky
  goal: "#f87171",                // red
  skill: "#34d399",               // emerald
  project: "#fbbf24",             // amber
  habit: "#a78bfa",               // violet
  belief: "#f472b6",              // pink
  relationship: "#2dd4bf",        // teal
  communication_style: "#94a3b8", // slate
};

export const DIMENSION_ORDER = Object.keys(DIMENSION_COLORS);

export function colorFor(dimension: string): string {
  return DIMENSION_COLORS[dimension] ?? "#94a3b8";
}
