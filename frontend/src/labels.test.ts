import { describe, expect, it } from 'vitest';
import { accountLabel, journalLabel, taskLabel } from 'src/labels';

describe('labels', () => {
  it('turns the schema vocabulary into words', () => {
    expect(accountLabel('program_funding')).toBe('Program fund');
    expect(journalLabel('transfer_reversed')).toBe('Reversed');
    expect(taskLabel('settle_transfer')).toBe('Check settlement');
  });

  it('shows an unknown identifier rather than hiding it', () => {
    // A kind this file has not heard of is a gap to notice, not to paper over.
    expect(accountLabel('fx_clearing')).toBe('fx_clearing');
    expect(journalLabel('fx_leg')).toBe('fx_leg');
    expect(taskLabel('export_report')).toBe('export_report');
  });
});
