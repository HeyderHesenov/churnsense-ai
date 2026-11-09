import type { Schemas } from '../api/client';

/** The fewest customers a group needs for its churn to be shown (the API's ``MIN_CELL``),
 * for the responses that do not serve it. */
export const MIN_CUSTOMERS = 20;

/** Why a group's churn is not shown, in the words a page puts in its place. */
export function hiddenText(cell: Schemas['SuppressedCell'], minimum: number): string {
  if (cell.n === 0) return 'no customers';
  if (cell.reason === 'complementary')
    return 'not shown here, so this table gives no smaller group away';
  return `fewer than ${minimum} customers`;
}
