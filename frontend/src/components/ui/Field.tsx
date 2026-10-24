/**
 * Form controls the pages filter with. Every control has a real label; where
 * the design shows none, the label is visually hidden, never left out.
 */
import { useId } from 'react';
import { cx } from '../../lib/style';
import { icons } from '../icons';
import type { Option } from './Toggle';
import styles from './Field.module.css';

interface SearchFieldProps {
  label: string;
  value: string;
  onChange: (value: string) => void;
  placeholder: string;
  maxLength?: number;
  className?: string | undefined;
}

/** The header's search box: icon, monospace text, label for assistive technology only. */
export function SearchField({
  label,
  value,
  onChange,
  placeholder,
  maxLength,
  className,
}: SearchFieldProps) {
  const id = useId();
  return (
    <div className={cx(styles.search, className)}>
      <icons.search size={16} strokeWidth={2} className={styles.searchIcon} />
      <label htmlFor={id} className="sr">
        {label}
      </label>
      <input
        id={id}
        type="search"
        className={styles.searchInput}
        value={value}
        placeholder={placeholder}
        maxLength={maxLength}
        autoComplete="off"
        spellCheck={false}
        onChange={(event) => onChange(event.target.value)}
      />
    </div>
  );
}

interface SelectProps<T extends string> {
  label: string;
  value: T;
  options: readonly Option<T>[];
  onChange: (value: T) => void;
  /** Label beside the control (a filter bar) or above it (a form). */
  layout?: 'inline' | 'stacked';
}

export function Select<T extends string>({
  label,
  value,
  options,
  onChange,
  layout = 'inline',
}: SelectProps<T>) {
  const id = useId();
  const choose = (raw: string) => {
    const option = options.find((o) => o.value === raw);
    if (option) onChange(option.value);
  };
  return (
    <span className={cx(styles.field, layout === 'stacked' && styles.stacked)}>
      <label htmlFor={id} className={styles.label}>
        {label}
      </label>
      <select
        id={id}
        className={styles.select}
        value={value}
        onChange={(event) => choose(event.target.value)}
      >
        {options.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
    </span>
  );
}
