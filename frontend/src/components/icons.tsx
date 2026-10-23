/** The design's line icons, drawn at 24 x 24 and sized by the caller. Decorative. */
import type { ReactNode, SVGProps } from 'react';

type IconProps = { size?: number; strokeWidth?: number } & Omit<
  SVGProps<SVGSVGElement>,
  'children'
>;

function Icon({
  size = 17,
  strokeWidth = 1.8,
  children,
  ...props
}: IconProps & { children: ReactNode }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={strokeWidth}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
      {...props}
    >
      {children}
    </svg>
  );
}

export const icons = {
  overview: (p: IconProps) => (
    <Icon {...p}>
      <rect x="3" y="3" width="7" height="9" rx="1.5" />
      <rect x="14" y="3" width="7" height="5" rx="1.5" />
      <rect x="14" y="12" width="7" height="9" rx="1.5" />
      <rect x="3" y="16" width="7" height="5" rx="1.5" />
    </Icon>
  ),
  explorer: (p: IconProps) => (
    <Icon {...p}>
      <circle cx="9" cy="8" r="3.5" />
      <path d="M2.5 20c.8-3.6 3.4-5.5 6.5-5.5s5.7 1.9 6.5 5.5" />
      <path d="M16 4.6a3.5 3.5 0 0 1 0 6.8M18.5 14.8c1.6.8 2.6 2.5 3 5.2" />
    </Icon>
  ),
  patterns: (p: IconProps) => (
    <Icon {...p}>
      <path d="M4 20V10M10 20V4M16 20v-7M22 20H2" />
    </Icon>
  ),
  performance: (p: IconProps) => (
    <Icon {...p}>
      <path d="M3 14a9 9 0 0 1 18 0" />
      <path d="M12 14l4.5-4.5" />
      <circle cx="12" cy="14" r="1.4" />
      <path d="M3 19h18" />
    </Icon>
  ),
  explainability: (p: IconProps) => (
    <Icon {...p}>
      <path d="M4 5h6M10 9h5M8 13h4M12 17h8M4 21h16" />
    </Icon>
  ),
  customer: (p: IconProps) => (
    <Icon {...p}>
      <circle cx="12" cy="8" r="4" />
      <path d="M4 21c1-4 4.2-6 8-6s7 2 8 6" />
    </Icon>
  ),
  simulator: (p: IconProps) => (
    <Icon {...p}>
      <path d="M4 7h10M18 7h2M4 17h4M12 17h8" />
      <circle cx="16" cy="7" r="2" />
      <circle cx="10" cy="17" r="2" />
    </Icon>
  ),
  batch: (p: IconProps) => (
    <Icon {...p}>
      <path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z" />
      <path d="M14 3v5h5M12 18v-6M9 15l3-3 3 3" />
    </Icon>
  ),
  close: (p: IconProps) => (
    <Icon strokeWidth={2.2} {...p}>
      <path d="M6 6l12 12M18 6L6 18" />
    </Icon>
  ),
  menu: (p: IconProps) => (
    <Icon strokeWidth={2} {...p}>
      <path d="M4 7h16M4 12h16M4 17h10" />
    </Icon>
  ),
  sidebar: (p: IconProps) => (
    <Icon {...p}>
      <rect x="3" y="4" width="18" height="16" rx="2.5" />
      <path d="M9 4v16" />
    </Icon>
  ),
  search: (p: IconProps) => (
    <Icon strokeWidth={2} {...p}>
      <circle cx="11" cy="11" r="7" />
      <path d="M20 20l-3.5-3.5" />
    </Icon>
  ),
  download: (p: IconProps) => (
    <Icon strokeWidth={2} {...p}>
      <path d="M12 4v11M7 10l5 5 5-5M5 20h14" />
    </Icon>
  ),
  arrowRight: (p: IconProps) => (
    <Icon strokeWidth={2} {...p}>
      <path d="M5 12h14M13 6l6 6-6 6" />
    </Icon>
  ),
  arrowUpRight: (p: IconProps) => (
    <Icon strokeWidth={2} {...p}>
      <path d="M7 17L17 7M9 7h8v8" />
    </Icon>
  ),
  chevronLeft: (p: IconProps) => (
    <Icon strokeWidth={2} {...p}>
      <path d="M15 6l-6 6 6 6" />
    </Icon>
  ),
  chevronRight: (p: IconProps) => (
    <Icon strokeWidth={2} {...p}>
      <path d="M9 6l6 6-6 6" />
    </Icon>
  ),
  chevronDown: (p: IconProps) => (
    <Icon strokeWidth={2.2} {...p}>
      <path d="M6 9l6 6 6-6" />
    </Icon>
  ),
  shield: (p: IconProps) => (
    <Icon strokeWidth={2.2} {...p}>
      <path d="M12 3l8 3v6c0 4.5-3.3 8-8 9-4.7-1-8-4.5-8-9V6z" />
      <path d="M8.5 12l2.5 2.5 4.5-5" />
    </Icon>
  ),
  info: (p: IconProps) => (
    <Icon strokeWidth={2} {...p}>
      <circle cx="12" cy="12" r="9" />
      <path d="M12 11v5M12 8h.01" />
    </Icon>
  ),
  warning: (p: IconProps) => (
    <Icon strokeWidth={2} {...p}>
      <path d="M12 3l9.5 17h-19z" />
      <path d="M12 10v4M12 17h.01" />
    </Icon>
  ),
  flag: (p: IconProps) => (
    <Icon strokeWidth={2.2} {...p}>
      <path d="M5 21V4M5 4h11l-2 4 2 4H5" />
    </Icon>
  ),
  plus: (p: IconProps) => (
    <Icon strokeWidth={2.2} {...p}>
      <path d="M12 5v14M5 12h14" />
    </Icon>
  ),
  pin: (p: IconProps) => (
    <Icon strokeWidth={2} {...p}>
      <path d="M9 4h6l-1 6 4 4H6l4-4z" />
      <path d="M12 14v7" />
    </Icon>
  ),
  swap: (p: IconProps) => (
    <Icon strokeWidth={2} {...p}>
      <path d="M7 4v14M3 14l4 4 4-4M17 20V6M13 10l4-4 4 4" />
    </Icon>
  ),
  file: (p: IconProps) => (
    <Icon strokeWidth={2} {...p}>
      <path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z" />
      <path d="M14 3v5h5" />
    </Icon>
  ),
  upload: (p: IconProps) => (
    <Icon {...p}>
      <path d="M12 16V4M7 9l5-5 5 5" />
      <path d="M4 16v3a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-3" />
    </Icon>
  ),
  emptySearch: (p: IconProps) => (
    <Icon strokeWidth={1.6} {...p}>
      <circle cx="11" cy="11" r="7" />
      <path d="M20 20l-3.5-3.5M8.5 11h5" />
    </Icon>
  ),
};

/** The ChurnSense mark: a ring with a gap, a dot in amber, a core in white. */
export function Logo({ size = 30 }: { size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 32 32"
      fill="none"
      aria-hidden="true"
      focusable="false"
    >
      <circle
        cx="16"
        cy="16"
        r="11"
        stroke="var(--accent)"
        strokeWidth="3"
        strokeDasharray="58 12"
        strokeLinecap="round"
        transform="rotate(-62 16 16)"
      />
      <circle cx="25.2" cy="9.6" r="2.6" fill="var(--sim)" />
      <circle cx="16" cy="16" r="3.2" fill="var(--ink)" />
    </svg>
  );
}
