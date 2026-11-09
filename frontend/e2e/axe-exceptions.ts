/**
 * Accessibility findings accepted on purpose. Each needs the rule, the element,
 * why it is acceptable and when to look again; an entry without all four is a
 * finding to fix, not to list. Empty: the pages pass every rule checked.
 */
export interface AxeException {
  rule: string;
  /** A CSS selector of the element the finding is about. */
  target: string;
  reason: string;
  /** ISO date by which the exception is reviewed again. */
  reviewBy: string;
}

export const AXE_EXCEPTIONS: readonly AxeException[] = [];
