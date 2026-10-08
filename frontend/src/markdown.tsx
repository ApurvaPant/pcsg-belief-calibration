import type { ReactNode } from "react";

// Minimal, safe inline markdown for assistant replies: **bold**, *italic*,
// _italic_ and `code`. Everything becomes real React text nodes — no
// dangerouslySetInnerHTML, so model output can never inject markup.
// Newlines survive via the bubble's `white-space: pre-wrap`.
const TOKEN = /(\*\*[^*\n]+\*\*|`[^`\n]+`|\*[^*\n]+\*|_[^_\n]+_)/g;

export function renderInline(text: string): ReactNode[] {
  const out: ReactNode[] = [];
  for (const [i, part] of text.split(TOKEN).entries()) {
    if (!part) continue;
    if (part.length >= 4 && part.startsWith("**") && part.endsWith("**")) {
      out.push(<strong key={i}>{part.slice(2, -2)}</strong>);
    } else if (part.length >= 2 && part.startsWith("`") && part.endsWith("`")) {
      out.push(<code key={i} className="md-code">{part.slice(1, -1)}</code>);
    } else if (
      part.length >= 2 &&
      ((part.startsWith("*") && part.endsWith("*")) || (part.startsWith("_") && part.endsWith("_")))
    ) {
      out.push(<em key={i}>{part.slice(1, -1)}</em>);
    } else {
      out.push(part);
    }
  }
  return out;
}
