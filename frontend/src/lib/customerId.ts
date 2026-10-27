/** A customer ID as the API's search accepts it: letters, digits and dashes, at most 32. */

export const MAX_CUSTOMER_ID = 32;
const NOT_IN_AN_ID = /[^A-Za-z0-9-]/g;

/** Typed text with every character an ID cannot hold dropped, cut to the longest ID. */
export function cleanCustomerId(text: string): string {
  return text.replace(NOT_IN_AN_ID, '').slice(0, MAX_CUSTOMER_ID);
}
