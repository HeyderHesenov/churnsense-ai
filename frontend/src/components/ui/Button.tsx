/**
 * Every button on every page: one look per variant, one box per size, as the
 * artboards draw them. A ``Button`` is never a form submit unless asked; a
 * ``ButtonLink`` is a real link (in-app with ``to``, a file with ``href``).
 */
import type { AnchorHTMLAttributes, ButtonHTMLAttributes, ReactNode } from 'react';
import { Link } from '../../app/router';
import { cx } from '../../lib/style';
import styles from './Button.module.css';

export type ButtonVariant =
  'primary' | 'sim' | 'secondary' | 'outline' | 'quiet' | 'simGhost' | 'status';
/** The box heights the design uses: 40, 36, 34 and 32 px. */
export type ButtonSize = 'lg' | 'md' | 'sm' | 'xs';

interface Look {
  variant?: ButtonVariant | undefined;
  size?: ButtonSize | undefined;
}

function look({ variant = 'secondary', size = 'md' }: Look, className?: string): string {
  return cx(styles.button, styles[variant], styles[size], className);
}

type ButtonProps = Look & ButtonHTMLAttributes<HTMLButtonElement>;

export function Button({ variant, size, className, type = 'button', ...props }: ButtonProps) {
  return <button type={type} className={look({ variant, size }, className)} {...props} />;
}

type IconButtonProps = Omit<ButtonProps, 'variant' | 'aria-label'> & {
  /** What the button does; an icon has no words of its own. */
  label: string;
  children: ReactNode;
};

export function IconButton({
  label,
  size = 'md',
  className,
  type = 'button',
  ...props
}: IconButtonProps) {
  return (
    <button
      type={type}
      aria-label={label}
      title={label}
      className={cx(styles.button, styles.icon, styles[size], className)}
      {...props}
    />
  );
}

type AnchorProps = Omit<AnchorHTMLAttributes<HTMLAnchorElement>, 'href'>;
type ButtonLinkProps = Look &
  AnchorProps &
  ({ to: string; href?: never } | { href: string; to?: never });

export function ButtonLink({
  variant,
  size,
  className,
  to,
  href,
  children,
  ...props
}: ButtonLinkProps) {
  const classes = look({ variant, size }, className);
  if (to !== undefined) {
    return (
      <Link to={to} className={classes} {...props}>
        {children}
      </Link>
    );
  }
  return (
    <a href={href} className={classes} {...props}>
      {children}
    </a>
  );
}
