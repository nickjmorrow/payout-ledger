import { describe, expect, it } from 'vitest';
import { errorDetail } from 'src/api/client';

const reply = (body: unknown, status = 422, statusText = '') =>
  new Response(body === null ? 'not json' : JSON.stringify(body), { status, statusText });

describe('errorDetail', () => {
  it('reads an HTTPException message as it is', async () => {
    expect(await errorDetail(reply({ detail: 'the program fund holds $10.00' }))).toBe(
      'the program fund holds $10.00',
    );
  });

  it('reads validation errors rather than skipping them', async () => {
    const body = { detail: [{ msg: 'Value error, a single payment is capped at $1,000,000.00' }] };
    expect(await errorDetail(reply(body))).toBe('a single payment is capped at $1,000,000.00');
  });

  it('never returns an empty message when there is nothing to read', async () => {
    // HTTP/2 carries no status text, so a proxy error would otherwise be blank.
    expect(await errorDetail(reply(null, 502))).toBe('Request failed (502)');
  });
});
