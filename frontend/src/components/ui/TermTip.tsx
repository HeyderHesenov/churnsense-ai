/**
 * A "?" after a technical term (PR-AUC, ECE, SHAP) that says in plain words
 * what it means.
 *
 * The definition is the button's accessible description: a screen reader reads
 * it with the button, whether or not it is shown. It shows on hover and on
 * keyboard focus; a press (a tap on a touch screen) keeps it open, and a second
 * press closes it. Escape closes it - and only it, not a drawer around it
 * (WCAG 1.4.13) - as do a press elsewhere and leaving with the focus; the
 * pointer can move onto it, and a press on its text, without closing it.
 *
 * It is never cut by the window: when it opens, and as the page scrolls or the
 * window changes size, it measures where it is, opens below rather than above
 * when it must, and slides sideways in eighths of its own width until it fits. Both are data attributes the stylesheet reads; no
 * position is written into the page. A container that clips its overflow
 * (a scrolling table) would still clip it: keep it out of one.
 */
import { useEffect, useId, useLayoutEffect, useRef, useState, type ReactNode } from 'react';
import styles from './TermTip.module.css';

interface TermTipProps {
  /** The term explained, as the reader sees it beside the button: "PR-AUC". */
  term: string;
  /** The plain-language definition. Text only: no links or controls. */
  children: ReactNode;
}

/** The space kept between the tip and the window's edge, and between it and its button. */
const MARGIN = 8;
const GAP = 8;
/** How far sideways the tip may slide, in eighths of its width: 0 (starts at the button) to 8. */
const STEPS = 8;

/** Where a tip of ``tip``'s size fits beside ``button`` in a window of the given size. */
export function placement(
  button: { top: number; bottom: number; left: number; width: number },
  tip: { width: number; height: number },
  viewport: { width: number; height: number },
): { side: 'above' | 'below'; shift: number } {
  const roomAbove = button.top - GAP - MARGIN;
  const roomBelow = viewport.height - button.bottom - GAP - MARGIN;
  const side =
    tip.height <= roomAbove || (tip.height > roomBelow && roomAbove >= roomBelow)
      ? 'above'
      : 'below';
  // The tip's left edge is the button's centre less shift/8 of its width.
  const centre = button.left + button.width / 2;
  const most = (centre - MARGIN) / tip.width; // further, and it passes the left edge
  const least = 1 - (viewport.width - MARGIN - centre) / tip.width; // less, the right edge
  const lowest = Math.max(0, Math.ceil(least * STEPS));
  const highest = Math.min(STEPS, Math.floor(most * STEPS));
  const centred = STEPS / 2;
  const shift =
    lowest <= highest
      ? Math.min(highest, Math.max(lowest, centred))
      : // Wider than the room: the step nearest the middle of the two limits.
        Math.min(STEPS, Math.max(0, Math.round(((least + most) / 2) * STEPS)));
  return { side, shift };
}

export function TermTip({ term, children }: TermTipProps) {
  const id = useId();
  const root = useRef<HTMLSpanElement>(null);
  const button = useRef<HTMLButtonElement>(null);
  const tip = useRef<HTMLSpanElement>(null);
  /** A press landed inside (on the tip's text): the blur it causes does not close it. */
  const pressedInside = useRef(false);
  const [hover, setHover] = useState(false);
  const [focus, setFocus] = useState(false);
  const [pinned, setPinned] = useState(false);
  const open = hover || focus || pinned;

  // Placed before it is painted, from its own size and its button's place; and
  // again whenever the page scrolls or the window changes size while it is open.
  useLayoutEffect(() => {
    if (!open) return;
    const place = () => {
      const node = tip.current;
      const anchor = button.current;
      if (!node || !anchor) return;
      const viewport = {
        width: document.documentElement.clientWidth || window.innerWidth,
        height: document.documentElement.clientHeight || window.innerHeight,
      };
      const { side, shift } = placement(
        anchor.getBoundingClientRect(),
        node.getBoundingClientRect(),
        viewport,
      );
      node.dataset.side = side;
      node.dataset.shift = String(shift);
    };
    place();
    window.addEventListener('resize', place);
    // Capture: a scroll inside any container scrolls the button too.
    window.addEventListener('scroll', place, { capture: true, passive: true });
    return () => {
      window.removeEventListener('resize', place);
      window.removeEventListener('scroll', place, { capture: true });
    };
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const shut = () => {
      setHover(false);
      setFocus(false);
      setPinned(false);
    };
    // The first Escape is the tip's alone: caught on the way down, before a
    // drawer or dialog around it hears it and closes too.
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return;
      event.stopPropagation();
      shut();
    };
    const onPointerDown = (event: PointerEvent) => {
      if (event.target instanceof Node && root.current?.contains(event.target)) {
        pressedInside.current = true;
        return;
      }
      shut();
    };
    // A press blurs between its down and its up: the mark lasts only that long.
    const onPointerUp = () => {
      pressedInside.current = false;
    };
    document.addEventListener('keydown', onKeyDown, true);
    document.addEventListener('pointerdown', onPointerDown, true);
    document.addEventListener('pointerup', onPointerUp, true);
    return () => {
      document.removeEventListener('keydown', onKeyDown, true);
      document.removeEventListener('pointerdown', onPointerDown, true);
      document.removeEventListener('pointerup', onPointerUp, true);
    };
  }, [open]);

  return (
    <span
      ref={root}
      className={styles.root}
      // A mouse's hover; a touch's "hover" is its tap, handled as a press.
      onPointerEnter={(event) => {
        if (event.pointerType === 'mouse') setHover(true);
      }}
      onPointerLeave={(event) => {
        if (event.pointerType === 'mouse') setHover(false);
      }}
    >
      <button
        ref={button}
        type="button"
        className={styles.button}
        aria-label={`About ${term}`}
        aria-describedby={id}
        onFocus={() => setFocus(true)}
        onBlur={() => {
          setFocus(false);
          // Leaving with the keyboard closes a pinned tip; a press on its own text does not.
          if (!pressedInside.current) setPinned(false);
        }}
        onClick={() => {
          if (!pinned) {
            setPinned(true);
            return;
          }
          setHover(false);
          setFocus(false);
          setPinned(false);
        }}
      >
        ?
      </button>
      <span ref={tip} id={id} role="tooltip" hidden={!open} className={styles.tip}>
        {children}
      </span>
    </span>
  );
}
