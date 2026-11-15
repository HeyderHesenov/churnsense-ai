import type { Schemas } from '../api/client';
import { hiddenText } from './cells';

type Reason = Schemas['SuppressedCell']['reason'];

function hidden(n: number, reason: Reason): Schemas['SuppressedCell'] {
  return { row: 'Two year', column: null, n, reason, suppressed: true };
}

describe('hiddenText', () => {
  it('says a small group is too small', () => {
    expect(hiddenText(hidden(12, 'too_few'), 20)).toBe('fewer than 20 customers');
  });

  it('says an empty group holds nobody', () => {
    expect(hiddenText(hidden(0, 'too_few'), 20)).toBe('no customers');
  });

  it('says why a group is withheld, without saying which of the two reasons it is', () => {
    expect(hiddenText(hidden(304, 'withheld'), 20)).toBe(
      'not shown: too few churners or stayers, or the tables together would give a group of fewer than 20 away',
    );
  });

  it('says why a level is withheld across partitions', () => {
    expect(hiddenText(hidden(1034, 'cross_partition'), 20)).toBe(
      'not shown: with an earlier release report it would give a small group away',
    );
  });

  it('names the minimum it is given', () => {
    expect(hiddenText(hidden(3, 'too_few'), 30)).toBe('fewer than 30 customers');
    expect(hiddenText(hidden(60, 'withheld'), 30)).toContain('fewer than 30 away');
  });
});
