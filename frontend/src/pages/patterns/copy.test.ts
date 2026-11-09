import { CAUSAL } from '../../test/wording';
import { contractAlone, contractByTenure, hidden, reported } from '../../test/patternsApi';
import {
  ASSOCIATIONS,
  cellName,
  cellText,
  explorerText,
  FAILED,
  focusText,
  lede,
  legendText,
  measureName,
  NOTE,
  population,
  rankedSubtitle,
  RANKED,
  showing,
  TITLE,
  WITHHELD,
} from './copy';

const VIEW = { rows: 'Contract', cols: 'tenure' };

describe('the lede when outcomes are withheld', () => {
  it('points to no list of outcomes the explorer is not showing', () => {
    expect(lede(20)).toContain('the Customer explorer lists training customers');
    const withheld = lede(20, true);
    expect(withheld).toContain('This is a display rule, not privacy protection.');
    expect(withheld).not.toContain('explorer');
  });
});

describe('the words of the patterns page', () => {
  it('describes the population the figures come from, and what is left out', () => {
    expect(population(contractByTenure())).toBe(
      'Training partition · 4,000 of 7,000 customers · 1,000 churned · 25.0% observed churn',
    );
    expect(population(contractByTenure({ base_rate: null }))).toBe(
      'Training partition · 4,000 of 7,000 customers · 1,000 churned',
    );
    const text = lede(20);
    expect(text).toContain('training partition');
    expect(text).toContain('Validation and test outcomes are not read');
    expect(text).toContain('No model output appears on this page');
    expect(text).toContain('fewer than 20 customers');
  });

  it('names a group by its dimensions and levels', () => {
    expect(cellName(VIEW, reported('Month-to-month', '0-3', 500, 250))).toBe(
      'Contract Month-to-month, tenure 0–3 months',
    );
    expect(
      cellName(
        { rows: 'monthly', cols: 'PaymentMethod' },
        reported('<$35', 'Credit card (automatic)', 50, 5),
      ),
    ).toBe('Monthly charges < $35, payment Credit card');
    expect(cellName({ rows: 'Contract', cols: null }, reported('Two year', null, 1000, 50))).toBe(
      'Contract Two year',
    );
  });

  it('states a reported group with its size, interval and comparison', () => {
    const data = contractByTenure();
    expect(focusText(VIEW, reported('One year', '4-72', 988, 148), data)).toBe(
      'Contract One year, tenure 4–72 months: 988 customers, 148 churned. ' +
        '15.0% observed churn (95% interval 11.9–17.9%), 0.60× the 25.0% of all 4,000 ' +
        'training customers, these included.',
    );
  });

  it('calls hiding a display rule, and does not deny what the explorer lists', () => {
    const text = lede(20);
    expect(text).toContain('display rule, not privacy protection');
    expect(text).toContain('the Customer explorer lists training customers with their outcomes');
    expect(explorerText({ kind: 'hidden' }, hidden('a', 'b', 12))).not.toMatch(
      /does not open|cannot be|not available|unavailable/i,
    );
  });

  it('says why a hidden group shows its size only', () => {
    const data = contractByTenure();
    expect(focusText(VIEW, hidden('One year', '0-3', 12, 'too_few'), data)).toBe(
      'Contract One year, tenure 0–3 months: 12 customers, fewer than 20, so no churn rate is shown.',
    );
    expect(focusText(VIEW, hidden('Month-to-month', '0-3', 960, 'complementary'), data)).toBe(
      'Contract Month-to-month, tenure 0–3 months: 960 customers. Its churn is held back on this page: ' +
        "with it, a smaller group's could be worked out from this table's totals.",
    );
    expect(focusText(VIEW, hidden('Two year', '0-3', 0), data)).toBe(
      'Contract Two year, tenure 0–3 months: no customers.',
    );
  });

  it('opens with the overall rate before anything is chosen', () => {
    expect(focusText(VIEW, null, contractByTenure())).toBe(
      'Overall observed churn in the training partition: 25.0% (1,000 of 4,000). ' +
        'Point at, focus or select a group.',
    );
  });

  it('writes each measure, and nothing for a hidden one but its size', () => {
    const cell = reported('One year', '4-72', 988, 148);
    expect(cellText(cell, 'rate')).toBe('15.0%');
    expect(cellText(cell, 'customers')).toBe('988');
    expect(cellText(cell, 'lift')).toBe('0.60×');
    expect(cellText({ ...cell, lift: null }, 'lift')).toBe('—');
    expect(cellText(hidden('a', 'b', 12), 'rate')).toBe('—');
    expect(cellText(hidden('a', 'b', 12), 'customers')).toBe('12');
    expect(measureName('lift')).toBe('Lift');
  });

  it('labels each legend with the scale it is drawn on', () => {
    expect(legendText({ measure: 'rate', top: 0.6 })).toEqual(['0%', '60%', 'observed churn']);
    expect(legendText({ measure: 'lift', top: 2 })).toEqual([
      '0.50×',
      '2.00×',
      '× the overall rate; 1× is the middle',
    ]);
    expect(legendText({ measure: 'customers', top: 1040 })).toEqual(['0', '1,040', 'customers']);
  });

  it('says why a group opens in the explorer or not', () => {
    expect(explorerText({ kind: 'link', href: '/explorer' }, reported('a', 'b', 988, 1))).toBe(
      'Open these 988 training customers in the customer explorer',
    );
    expect(explorerText({ kind: 'hidden' }, hidden('a', 'b', 12))).toBe(
      "A group whose churn is hidden on this page has no link to the explorer from here. The explorer's own filters still list training customers with their outcomes.",
    );
    expect(
      explorerText({ kind: 'unfiltered', dimension: 'monthly' }, reported('a', 'b', 40, 1)),
    ).toBe('The explorer cannot filter by monthly charges.');
  });

  it('says the ranking is chosen for its extremes and the intervals are one at a time', () => {
    const text = rankedSubtitle(5, 20);
    expect(text).toContain('highest of 5 reported groups');
    expect(text).toContain('more likely overstated than understated');
    expect(text).toContain('one group on its own, not all of them at once');
    expect(showing(contractByTenure(), { rows: 'InternetService', cols: 'PaymentMethod' })).toBe(
      'Showing Contract × tenure while Internet × payment loads.',
    );
    expect(showing(contractAlone(), { rows: 'Contract', cols: 'tenure' })).toBe(
      'Showing Contract while Contract × tenure loads.',
    );
  });

  it('makes no causal claim anywhere', () => {
    const data = contractByTenure();
    const texts = [
      TITLE,
      lede(20),
      FAILED,
      WITHHELD.title,
      WITHHELD.body,
      NOTE,
      ...Object.values(RANKED),
      ...Object.values(ASSOCIATIONS),
      rankedSubtitle(5, 20),
      population(data),
      focusText(VIEW, null, data),
      ...data.cells.map((c) => focusText(VIEW, c, data)),
    ];
    for (const text of texts) {
      expect(text).not.toMatch(CAUSAL);
      expect(text).not.toMatch(/significan|predict|probabilit|\brisk\b/i);
    }
  });
});
