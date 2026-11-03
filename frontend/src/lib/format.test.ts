import {
  cents,
  cut,
  int,
  minorDigits,
  money,
  moneyK,
  moneyShort,
  months,
  pct,
  price,
  probability,
  probabilityPct,
  signedMoney,
  shownSign,
  signedPoints,
  times,
} from './format';

describe('format', () => {
  it('writes counts with separators', () => {
    expect(int(7043)).toBe('7,043');
    expect(int(2305.6)).toBe('2,306');
  });

  it('writes shares as percentages', () => {
    expect(pct(0.2653532)).toBe('26.5%');
    expect(pct(0.32741, 2)).toBe('32.74%');
  });

  it('never rounds a clamped probability to certainty', () => {
    expect(probability(0.97)).toBe('0.970');
    expect(probability(0.9999)).toBe('0.9999');
    expect(probability(0.99988)).toBe('0.9999');
    expect(probability(0.000118)).toBe('0.0001');
    expect(probability(0.999)).toBe('0.999');
    expect(probabilityPct(0.802979)).toBe('80.3%');
    expect(probabilityPct(0.99988)).toBe('99.99%');
    expect(probabilityPct(0.000118)).toBe('0.01%');
  });

  it('writes money in whole dollars with a true minus', () => {
    expect(money(456116.6)).toBe('$456,117');
    expect(money(-4099.53)).toBe('−$4,100');
    expect(money(-0.2)).toBe('$0');
    expect(cents(105.9)).toBe('$105.90');
    expect(cents(1874.45)).toBe('$1,874.45');
    expect(signedMoney(32)).toBe('+$32');
    expect(signedMoney(-1200)).toBe('−$1,200');
  });

  it('writes compact axis money', () => {
    expect(moneyShort(26579)).toBe('$26.6k');
    expect(moneyShort(30000)).toBe('$30k');
    expect(moneyShort(950)).toBe('$950');
    expect(moneyShort(-5000)).toBe('−$5k');
    expect(moneyShort(0)).toBe('$0');
  });

  it('writes signed percentage points and other small figures', () => {
    expect(signedPoints(0.317)).toBe('+31.7');
    expect(signedPoints(-0.209)).toBe('−20.9');
    // The sign a reader sees: one that rounds to nothing has none.
    expect(shownSign(0.317)).toBe(1);
    expect(shownSign(-0.209)).toBe(-1);
    expect(shownSign(0.0004)).toBe(0);
    expect(shownSign(-0.0004)).toBe(0);
    expect(shownSign(0.0005)).toBe(1);
    expect(shownSign(-0.0005)).toBe(-1);
    expect(shownSign(0)).toBe(0);
    expect(cut(0.36)).toBe('0.36');
    expect(times(2.358)).toBe('2.36×');
    expect(months(1)).toBe('1 month');
    expect(months(3)).toBe('3 months');
  });

  it('never writes a negative zero, and uses a true minus for negatives', () => {
    expect(int(-0.4)).toBe('0');
    expect(int(-1200)).toBe('−1,200');
    expect(pct(-0.0004)).toBe('0.0%');
    expect(pct(-0.032)).toBe('−3.2%');
    expect(signedPoints(0)).toBe('0.0');
    expect(signedPoints(-0.0004)).toBe('0.0');
    expect(signedPoints(0.0006)).toBe('+0.1');
  });

  it('switches compact money to thousands at the rounded value', () => {
    expect(moneyShort(999.6)).toBe('$1k');
    expect(moneyShort(999.4)).toBe('$999');
    expect(moneyShort(-999.6)).toBe('−$1k');
  });

  it('writes thousands of dollars with one decimal for a card', () => {
    expect(moneyK(226_179)).toBe('$226.2k');
    expect(moneyK(220_000)).toBe('$220.0k');
    expect(moneyK(-1_500)).toBe('−$1.5k');
  });
  it('writes money in the currency it is given, dollars when none is', () => {
    expect(money(1234.5, 'EUR')).toBe('€1,235');
    expect(money(-1234, 'EUR')).toBe('−€1,234');
    expect(money(-0.2, 'EUR')).toBe('€0');
    expect(signedMoney(32, 'EUR')).toBe('+€32');
    expect(signedMoney(-1200, 'GBP')).toBe('−£1,200');
    expect(cents(105.9, 'EUR')).toBe('€105.90');
    expect(moneyShort(26579, 'EUR')).toBe('€26.6k');
    expect(money(5, 'CHF')).toBe('CHF\u00a05');
  });

  it('writes an amount to its currency’s own minor unit', () => {
    expect(minorDigits('USD')).toBe(2);
    expect(minorDigits('JPY')).toBe(0);
    expect(minorDigits('KWD')).toBe(3);
    expect(minorDigits('not a code')).toBe(2);
    expect(cents(1250, 'JPY')).toBe('¥1,250');
    expect(cents(1.5, 'KWD')).toBe('KWD\u00a01.500');
    expect(price(1250, 'JPY')).toBe('¥1,250');
    expect(price(1.125, 'KWD')).toBe('KWD\u00a01.125');
    expect(price(2, 'KWD')).toBe('KWD\u00a02');
  });

  it('writes no sign at all for an empty currency', () => {
    expect(money(5, '')).toBe('5');
  });

  it('writes a currency that is not a currency code as the text it was given', () => {
    expect(money(5, 'not a code')).toBe('not a code\u00a05');
    expect(moneyShort(-5000, 'not a code')).toBe('−not a code\u00a05k');
  });

  it('writes compact money in millions once thousands would reach a thousand', () => {
    expect(moneyShort(14_090_000)).toBe('$14.1M');
    expect(moneyShort(-14_090_000)).toBe('−$14.1M');
    expect(moneyShort(2_000_000)).toBe('$2M');
    expect(moneyShort(999_949)).toBe('$999.9k');
    expect(moneyShort(999_950)).toBe('$1M');
    expect(moneyShort(-999_950)).toBe('−$1M');
  });

  it('writes a price with cents only when it has some', () => {
    expect(price(50)).toBe('$50');
    expect(price(12.5)).toBe('$12.50');
    expect(price(0.01)).toBe('$0.01');
    expect(price(0)).toBe('$0');
    expect(price(10_000)).toBe('$10,000');
    expect(price(49.999)).toBe('$50');
    expect(price(7.5, 'EUR')).toBe('€7.50');
  });
});
