import { describe, expect, it } from 'vitest';
import { formatMinor, formatMoney, parseMajor } from 'src/money';

describe('formatMinor', () => {
  it('always shows two decimal places', () => {
    expect(formatMinor(250_000)).toBe('2,500.00');
    expect(formatMinor(5)).toBe('0.05');
    expect(formatMinor(0)).toBe('0.00');
  });

  it('formats a whole program balance as dollars', () => {
    expect(formatMoney(100_000_000, 'USD')).toBe('$1,000,000.00');
    expect(formatMoney(50_000, 'USD')).toBe('$500.00');
  });

  it('names any other currency by its code rather than guessing a symbol', () => {
    // Intl separates the code from the number with a no-break space.
    expect(formatMoney(250_000, 'KES')).toBe('KES\u{A0}2,500.00');
  });
});

describe('parseMajor', () => {
  it('converts what someone typed into minor units', () => {
    expect(parseMajor('2500')).toBe(250_000);
    expect(parseMajor('2500.5')).toBe(250_050);
    expect(parseMajor('0.05')).toBe(5);
    expect(parseMajor(' 12.34 ')).toBe(1234);
  });

  it('rounds rather than truncating at the last step', () => {
    // 25.005 * 100 is 2500.4999999999995 in binary floating point. Truncating
    // would charge a cent less than the person asked for.
    expect(parseMajor('25.01')).toBe(2501);
    expect(parseMajor('0.10')).toBe(10);
  });

  it('returns null for anything that is not a plain amount', () => {
    // Null rather than 0: zero is a real amount the API rejects with a useful
    // message, and conflating the two would show the wrong error.
    expect(parseMajor('')).toBeNull();
    expect(parseMajor('abc')).toBeNull();
    expect(parseMajor('1e3')).toBeNull();
    expect(parseMajor('-5')).toBeNull();
    expect(parseMajor('1.234')).toBeNull();
  });
});
