/**
 * The configured risk bands, as pages name them in an address. A band's range
 * is the API's (``/api/model-info``); these are only its names, in order.
 */

export const BAND_NAMES = ['Low', 'Moderate', 'High', 'Critical'] as const;
export type BandName = (typeof BAND_NAMES)[number];

export function isBandName(name: string): name is BandName {
  return (BAND_NAMES as readonly string[]).includes(name);
}

/** Probabilities never exceed 1; the top band's stored bound (1.01) is a closed-interval device. */
export function bandTop(band: { max: number }): number {
  return Math.min(band.max, 1);
}
