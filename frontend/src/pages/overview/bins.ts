/** The histogram bin under a pointer at ``clientX``, over a plot ``width`` wide; null for no plot. */
export function binIndex(
  clientX: number,
  left: number,
  width: number,
  count: number,
): number | null {
  if (width <= 0 || count <= 0) return null;
  const bin = Math.floor(((clientX - left) / width) * count);
  return Math.max(0, Math.min(count - 1, bin));
}
