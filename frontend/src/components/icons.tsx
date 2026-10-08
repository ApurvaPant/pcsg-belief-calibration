import type { ReactNode, SVGProps } from "react";

// One consistent line-icon system: 24-grid, currentColor stroke, round joins.
// No emoji, no gradients — icons inherit the colour of whatever they sit in.
interface IconProps extends Omit<SVGProps<SVGSVGElement>, "children"> {
  size?: number;
}

function Base({ size = 16, children, ...rest }: IconProps & { children: ReactNode }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.6}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
      {...rest}
    >
      {children}
    </svg>
  );
}

export const IconPencil = (p: IconProps) => (
  <Base {...p}>
    <path d="M16.5 4.5a2.1 2.1 0 0 1 3 3L8 19l-4 1 1-4z" />
    <path d="M14 7l3 3" />
  </Base>
);

export const IconTrash = (p: IconProps) => (
  <Base {...p}>
    <path d="M4 7h16" />
    <path d="M9 7V5.2A1.2 1.2 0 0 1 10.2 4h3.6A1.2 1.2 0 0 1 15 5.2V7" />
    <path d="M6.5 7l.9 12a2 2 0 0 0 2 1.9h5.2a2 2 0 0 0 2-1.9L17.5 7" />
    <path d="M10 11v6M14 11v6" />
  </Base>
);

export const IconUndo = (p: IconProps) => (
  <Base {...p}>
    <path d="M4 8h9.5a5.5 5.5 0 1 1 0 11H8" />
    <path d="M4 8l3.5-3.5M4 8l3.5 3.5" />
  </Base>
);

// A small 3-node graph — the product's own mark, reused for memory + brand.
export const IconMemory = (p: IconProps) => (
  <Base {...p}>
    <circle cx="6" cy="7" r="2.1" />
    <circle cx="18" cy="7" r="2.1" />
    <circle cx="12" cy="17" r="2.1" />
    <path d="M7.9 7h8.2M7.4 8.7l3.1 6.6M16.6 8.7l-3.1 6.6" />
  </Base>
);

export const IconGraph = (p: IconProps) => (
  <Base {...p}>
    <circle cx="6" cy="12" r="2.1" />
    <circle cx="18" cy="6" r="2.1" />
    <circle cx="18" cy="18" r="2.1" />
    <path d="M7.9 11l8.2-4M7.9 13l8.2 4" />
  </Base>
);

// Compact brand mark for avatars/logos (filled nodes read better small).
export const IconMark = ({ size = 16, ...rest }: IconProps) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="currentColor" aria-hidden {...rest}>
    <circle cx="12" cy="5.5" r="2.4" />
    <circle cx="5.5" cy="17" r="2.4" />
    <circle cx="18.5" cy="17" r="2.4" />
    <path d="M12 8.2 6.3 15M12 8.2 17.7 15" stroke="currentColor" strokeWidth={1.5} fill="none" />
  </svg>
);

const DIM_PATHS: Record<string, ReactNode> = {
  identity: (<><circle cx="12" cy="8" r="3.2" /><path d="M5.5 19.2a6.5 6.5 0 0 1 13 0" /></>),
  preference: (<path d="M12 20s-6.6-4.2-8.7-8.1A4.6 4.6 0 0 1 12 6.1a4.6 4.6 0 0 1 8.7 5.8C18.6 15.8 12 20 12 20z" />),
  interest: (<><circle cx="12" cy="12" r="8.4" /><path d="M15.4 8.6 13 13l-4.4 2.4L11 11z" /></>),
  goal: (<><circle cx="12" cy="12" r="8.4" /><circle cx="12" cy="12" r="4" /><circle cx="12" cy="12" r="0.6" /></>),
  skill: (<path d="M13 2.5 4.5 13.5H11l-1 8 8.5-11.5H12z" />),
  project: (<><path d="M12 2.6 20 7v10l-8 4.4L4 17V7z" /><path d="M4 7l8 4.4L20 7" /><path d="M12 11.4V21" /></>),
  habit: (<><path d="M17 4.5l3 3-3 3" /><path d="M20 7.5H9.5A5.5 5.5 0 0 0 4 13" /><path d="M7 19.5l-3-3 3-3" /><path d="M4 16.5h10.5A5.5 5.5 0 0 0 20 11" /></>),
  belief: (<><path d="M9.5 18.5h5M10.5 21h3" /><path d="M12 3a6 6 0 0 0-3.9 10.5c.8.7 1.1 1.3 1.1 2.5h5.6c0-1.2.3-1.8 1.1-2.5A6 6 0 0 0 12 3z" /></>),
  relationship: (<><circle cx="9" cy="8.5" r="2.9" /><path d="M3.6 19a5.4 5.4 0 0 1 10.8 0" /><path d="M15.5 6.1a2.9 2.9 0 0 1 0 5.3" /><path d="M17 13.4a5.4 5.4 0 0 1 3.4 5" /></>),
  communication_style: (<path d="M20 14.5a2 2 0 0 1-2 2H9l-4 3.2V6.5a2 2 0 0 1 2-2h11a2 2 0 0 1 2 2z" />),
};

export function DimensionIcon({ name, size = 16, ...rest }: IconProps & { name: string }) {
  return <Base size={size} {...rest}>{DIM_PATHS[name] ?? <circle cx="12" cy="12" r="7.5" />}</Base>;
}
