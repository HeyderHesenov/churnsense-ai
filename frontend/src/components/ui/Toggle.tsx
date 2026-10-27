/**
 * Controls that are on or off: a chip, a removable chip, and a segmented
 * control (one of a few). Each says its state with ``aria-pressed``; the
 * caller owns the state.
 */
import type { ReactNode } from 'react';
import { cx } from '../../lib/style';
import styles from './Toggle.module.css';

interface ToggleChipProps {
  pressed: boolean;
  onToggle: () => void;
  children: ReactNode;
  /** A figure beside the name, such as how many customers the filter keeps. */
  count?: string;
  /** A risk band: shows its swatch, and with ``variant="band"`` its colours when on. */
  band?: string;
  variant?: 'filter' | 'preset' | 'band';
  label?: string;
}

export function ToggleChip({
  pressed,
  onToggle,
  children,
  count,
  band,
  variant = 'filter',
  label,
}: ToggleChipProps) {
  return (
    <button
      type="button"
      className={cx(
        styles.chip,
        variant === 'preset' && styles.preset,
        variant === 'band' && styles.band,
      )}
      aria-pressed={pressed}
      aria-label={label}
      data-band={band}
      onClick={onToggle}
    >
      {band !== undefined && <span className={styles.swatch} aria-hidden="true" />}
      {children}
      {/* The space keeps the name "Critical 405", not "Critical405"; the flex gap draws it. */}
      {count !== undefined && (
        <>
          {' '}
          <span className={styles.count}>{count}</span>
        </>
      )}
    </button>
  );
}

interface RemovableChipProps {
  /** What removing it does, e.g. "Clear search". */
  label: string;
  onRemove: () => void;
  children: ReactNode;
}

export function RemovableChip({ label, onRemove, children }: RemovableChipProps) {
  return (
    <button type="button" className={cx(styles.chip, styles.removable)} onClick={onRemove}>
      {/* The name keeps the chip's visible words after the action (label in name). */}
      <span className="sr">{label}:</span> {children}
      <span aria-hidden="true">×</span>
    </button>
  );
}

export interface Option<T extends string> {
  value: T;
  label: string;
}

interface SegmentedProps<T extends string> {
  label: string;
  options: readonly Option<T>[];
  value: T;
  onChange: (value: T) => void;
  /** 30 px buttons instead of 32. */
  small?: boolean;
  /** The lighter frame used inside a filter bar. */
  compact?: boolean;
}

export function Segmented<T extends string>({
  label,
  options,
  value,
  onChange,
  small,
  compact,
}: SegmentedProps<T>) {
  return (
    <div
      role="group"
      aria-label={label}
      className={cx(styles.segmented, small && styles.small, compact && styles.compact)}
    >
      {options.map((option) => (
        <button
          key={option.value}
          type="button"
          className={styles.seg}
          aria-pressed={option.value === value}
          onClick={() => onChange(option.value)}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}
