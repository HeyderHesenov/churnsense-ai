/**
 * A figure that counts up to its value: from 0 on first showing, then from
 * wherever it stands to each new value, over ``duration`` ms with an
 * ease-out-cubic curve, one step per animation frame.
 *
 * Only the drawing moves. The value is the caller's, untouched: the last frame
 * is exactly ``value``, not a sum of steps, and a reader who asked for less
 * motion gets ``value`` at once, never a frame in between. A change of value
 * part-way starts again from where the figure stands; leaving the page cancels
 * the frame that was waiting. ``null`` (no figure) is shown as it is.
 */
import { useEffect, useRef, useState } from 'react';
import { REDUCED_MOTION, useMediaQuery } from './useMediaQuery';

export const COUNT_UP_MS = 1100;

/** ease-out-cubic: fast at first, settling at the end. */
export function easeOutCubic(t: number): number {
  return 1 - (1 - t) ** 3;
}

export function useCountUp(value: number | null, duration: number = COUNT_UP_MS): number | null {
  const reduce = useMediaQuery(REDUCED_MOTION);
  const animate = !reduce && value !== null;
  const [shown, setShown] = useState<number>(0);
  // Where the figure stands, read when a new value arrives.
  const standing = useRef<number | null>(null);
  // Starting to move again (after no figure, or after reduced motion), the first
  // frame is where the figure stood - 0 after none, the value itself after a
  // still one - never a frame left over from before.
  const [was, setWas] = useState({ animate, value });
  if (was.animate !== animate || was.value !== value) {
    setWas({ animate, value });
    if (animate && !was.animate) setShown(was.value ?? 0);
  }

  useEffect(() => {
    if (!animate) {
      standing.current = value;
      return;
    }
    const from = standing.current ?? 0;
    let frame = 0;
    if (from === value || duration <= 0) {
      frame = requestAnimationFrame(() => {
        standing.current = value;
        setShown(value);
      });
      return () => cancelAnimationFrame(frame);
    }
    let start: number | null = null;
    const step = (now: number) => {
      start ??= now;
      const t = Math.min(1, (now - start) / duration);
      const next = t >= 1 ? value : from + (value - from) * easeOutCubic(t);
      standing.current = next;
      setShown(next);
      if (t < 1) frame = requestAnimationFrame(step);
    };
    frame = requestAnimationFrame(step);
    return () => cancelAnimationFrame(frame);
  }, [animate, value, duration]);

  return animate ? shown : value;
}
