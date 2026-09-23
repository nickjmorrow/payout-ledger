/**
 * Money, in and out of minor units. Pure, no React.
 *
 * **The wire carries minor units — integers — and nothing here changes that.**
 * These functions are for display and for parsing what somebody typed; the
 * value that travels to the API is always the integer. A float that has been
 * through `2500.10` and back cannot be trusted to still be 250010, and a
 * ledger that cannot add up its own rows exactly is not a ledger.
 */

const MINOR_PER_MAJOR = 100;

/**
 * US English, pinned rather than taken from the reader's browser. An amount is
 * data here, not prose: the same balance showing as `1.000.000,00` on one
 * operator's screen and `1,000,000.00` on another's is how a figure gets read
 * back wrong over the phone.
 */
const LOCALE = 'en-US';

/** `250000` -> `"2,500.00"`, without the currency. For columns under a heading that names it. */
export function formatMinor(minor: number): string {
  return (minor / MINOR_PER_MAJOR).toLocaleString(LOCALE, {
    maximumFractionDigits: 2,
    minimumFractionDigits: 2,
  });
}

/** `250000, 'USD'` -> `"$2,500.00"`. Any other currency is shown by its code. */
export function formatMoney(minor: number, currency: string): string {
  return (minor / MINOR_PER_MAJOR).toLocaleString(LOCALE, { currency, style: 'currency' });
}

/**
 * What somebody typed, as minor units, or null if it is not an amount.
 *
 * Rounded rather than truncated, and rounded at the very end: `parseFloat`
 * gives `25.005 * 100 === 2500.4999999999995`, and `Math.trunc` would quietly
 * charge a cent less. Returning null rather than 0 for junk matters too — zero
 * is a real amount the API will reject with a useful message, and conflating
 * the two would show the wrong error.
 */
export function parseMajor(input: string): number | null {
  const trimmed = input.trim();
  if (trimmed === '') return null;

  // Rejected explicitly: `Number('')` is 0, `Number('1e3')` is 1000, and
  // neither is something a person meant to type into an amount field.
  if (!/^\d+(?:\.\d{1,2})?$/.test(trimmed)) return null;

  return Math.round(Number(trimmed) * MINOR_PER_MAJOR);
}
