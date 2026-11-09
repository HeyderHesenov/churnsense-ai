import type { Schemas } from '../api/client';
import { hiddenText } from './cells';

function hidden(n: number, reason: 'too_few' | 'complementary'): Schemas['SuppressedCell'] {
  return { row: 'Two year', column: null, n, reason, suppressed: true };
}

describe('hiddenText', () => {
  it('says a small group is too small', () => {
    expect(hiddenText(hidden(12, 'too_few'), 20)).toBe('fewer than 20 customers');
  });

  it('says an empty group holds nobody', () => {
    expect(hiddenText(hidden(0, 'too_few'), 20)).toBe('no customers');
  });

  it('says why a large group is held back', () => {
    expect(hiddenText(hidden(304, 'complementary'), 20)).toBe(
      'not shown here, so this table gives no smaller group away',
    );
  });

  it('names the minimum it is given', () => {
    expect(hiddenText(hidden(3, 'too_few'), 30)).toBe('fewer than 30 customers');
  });
});
