/**
 * Money in and out of minor units, for display and input only. Pure, no React.
 *
 * The wire always carries the integer.
 */

const MINOR_PER_MAJOR = 100;

/** Pinned to US English, so a figure reads the same on every operator's screen. */
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
 * Rounded at the last step (`25.005 * 100` is `2500.4999…`). Null rather than 0
 * for junk, because zero is a real amount the API refuses with its own message.
 */
export function parseMajor(input: string): number | null {
  const trimmed = input.trim();
  if (trimmed === '') return null;

  // `Number('')` is 0 and `Number('1e3')` is 1000; neither is an amount someone meant.
  if (!/^\d+(?:\.\d{1,2})?$/.test(trimmed)) return null;

  return Math.round(Number(trimmed) * MINOR_PER_MAJOR);
}
