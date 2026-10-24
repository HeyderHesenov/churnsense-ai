/**
 * A button that opens a small panel of facts beside it (the overview's
 * provenance). Not modal: the page stays usable. Focus moves into the panel
 * when it opens, so it is read next; it closes on Escape - giving focus back
 * to its button - or on a press anywhere outside.
 */
import { useEffect, useId, useRef, useState, type ReactNode, type Ref } from 'react';
import { cx } from '../../lib/style';
import styles from './Popover.module.css';

export interface TriggerProps {
  type: 'button';
  ref: Ref<HTMLButtonElement>;
  'aria-expanded': boolean;
  'aria-controls': string | undefined;
  onClick: () => void;
}

interface PopoverProps {
  /** The panel's accessible name. */
  label: string;
  /** Renders the button; spread the props onto it. */
  trigger: (props: TriggerProps) => ReactNode;
  children: ReactNode;
  className?: string | undefined;
}

export function Popover({ label, trigger, children, className }: PopoverProps) {
  const [open, setOpen] = useState(false);
  const id = useId();
  const root = useRef<HTMLSpanElement>(null);
  const button = useRef<HTMLButtonElement>(null);
  const panel = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    panel.current?.focus();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return;
      setOpen(false);
      button.current?.focus();
    };
    const onPointerDown = (event: PointerEvent) => {
      if (event.target instanceof Node && root.current?.contains(event.target)) return;
      setOpen(false);
    };
    document.addEventListener('keydown', onKeyDown);
    document.addEventListener('pointerdown', onPointerDown);
    return () => {
      document.removeEventListener('keydown', onKeyDown);
      document.removeEventListener('pointerdown', onPointerDown);
    };
  }, [open]);

  return (
    <span ref={root} className={styles.root}>
      {trigger({
        type: 'button',
        ref: button,
        'aria-expanded': open,
        'aria-controls': open ? id : undefined,
        onClick: () => setOpen(!open),
      })}
      {open && (
        <div
          ref={panel}
          id={id}
          role="dialog"
          aria-label={label}
          tabIndex={-1}
          className={cx(styles.panel, className)}
        >
          {children}
        </div>
      )}
    </span>
  );
}
