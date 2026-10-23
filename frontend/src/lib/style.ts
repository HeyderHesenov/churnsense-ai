import type { CSSProperties } from 'react';

/** Class names, the falsy ones dropped: ``cx(styles.chip, on && styles.on)``. */
export function cx(...names: (string | false | null | undefined)[]): string {
  return names.filter(Boolean).join(' ');
}

/**
 * Dynamic values as CSS custom properties: ``style={cssVars({ '--p': 0.36 })}``.
 * React sets them through the CSSOM, which the page's ``style-src 'self'``
 * policy allows; the stylesheet decides what each one means.
 */
export function cssVars(vars: Record<`--${string}`, string | number>): CSSProperties {
  return vars;
}
