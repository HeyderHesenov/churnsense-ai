import { act, renderHook } from '@testing-library/react';
import { hrefTo, param, useParams } from './params';

const BANDS = ['Low', 'Moderate', 'High', 'Critical'] as const;

const schema = {
  bands: param.list(BANDS, ['Critical', 'High', 'Moderate']),
  sort: param.choice(['probability', 'at_risk', 'tenure'] as const, 'at_risk'),
  part: param.optional(['train', 'validation', 'test'] as const),
  page: param.int({ min: 1, max: 1000, fallback: 1 }),
  q: param.text({ pattern: /^[A-Za-z0-9-]*$/, maxLength: 32 }),
};

function at(search: string) {
  window.history.replaceState(null, '', `/explorer${search}`);
  return renderHook(() => useParams(schema));
}

describe('useParams reading the address', () => {
  it('gives every default for an empty query', () => {
    const { result } = at('');
    expect(result.current[0]).toEqual({
      bands: ['Critical', 'High', 'Moderate'],
      sort: 'at_risk',
      part: null,
      page: 1,
      q: '',
    });
  });

  it('reads valid values', () => {
    const { result } = at('?bands=High&bands=Low&sort=tenure&part=test&page=3&q=DEMO-0001');
    expect(result.current[0]).toEqual({
      bands: ['Low', 'High'],
      sort: 'tenure',
      part: 'test',
      page: 3,
      q: 'DEMO-0001',
    });
  });

  it.each([
    ['an unknown choice', '?sort=churn', { sort: 'at_risk' }],
    ['an unknown optional choice', '?part=everyone', { part: null }],
    ['a page below the range', '?page=0', { page: 1 }],
    ['a page above the range', '?page=1001', { page: 1 }],
    ['a fractional page', '?page=2.5', { page: 1 }],
    ['a page with trailing text', '?page=2x', { page: 1 }],
    ['text outside the pattern', '?q=%3Cscript%3E', { q: '' }],
    ['text over the length limit', `?q=${'A'.repeat(33)}`, { q: '' }],
  ])('falls back to the default for %s', (_name, search, expected) => {
    const { result } = at(search);
    expect(result.current[0]).toMatchObject(expected);
  });

  it('drops unknown and repeated list members, and reads them in the declared order', () => {
    const { result } = at('?bands=High&bands=Severe&bands=High&bands=Low');
    expect(result.current[0].bands).toEqual(['Low', 'High']);
  });

  it('treats the default in another order as the default', () => {
    const { result } = at('?bands=High&bands=Moderate&bands=Critical&page=2');
    expect(result.current[0].bands).toEqual(['Critical', 'High', 'Moderate']);
    act(() => result.current[1]({ bands: ['Moderate', 'Critical', 'High'] }));
    expect(window.location.search).toBe('?page=2');
  });

  it('falls back to the default list when no member is valid', () => {
    const { result } = at('?bands=Severe');
    expect(result.current[0].bands).toEqual(['Critical', 'High', 'Moderate']);
  });

  it('reads an explicitly empty list as empty, not as the default', () => {
    const { result } = at('?bands=');
    expect(result.current[0].bands).toEqual([]);
  });

  it('refuses a stateful pattern, which would accept a value only every other time', () => {
    expect(() => param.text({ pattern: /^[A-Z]*$/g, maxLength: 8 })).toThrow(
      /without the g or y flag/,
    );
  });

  it('keeps the same values object while the address is unchanged', () => {
    const { result, rerender } = at('?page=2');
    const first = result.current[0];
    rerender();
    expect(result.current[0]).toBe(first);
  });
});

describe('a list whose default comes from the data', () => {
  const derived = { bands: param.list(BANDS, null) };

  function hook(search: string) {
    window.history.replaceState(null, '', `/${search}`);
    return renderHook(() => useParams(derived));
  }

  it('reads as null until something is chosen', () => {
    expect(hook('').result.current[0].bands).toBeNull();
    expect(hook('?bands=Severe').result.current[0].bands).toBeNull();
  });

  it('keeps a choice, an empty one included, and forgets it when set back to null', () => {
    const { result } = hook('?bands=High');
    expect(result.current[0].bands).toEqual(['High']);
    act(() => result.current[1]({ bands: [] }));
    expect(result.current[0].bands).toEqual([]);
    act(() => result.current[1]({ bands: null }));
    expect(window.location.search).toBe('');
    expect(result.current[0].bands).toBeNull();
  });
});

describe('useParams writing the address', () => {
  it('writes non-default values and leaves defaults out of the address', () => {
    const { result } = at('');
    act(() => result.current[1]({ sort: 'tenure', page: 1, bands: ['High', 'Low'] }));
    expect(window.location.search).toBe('?sort=tenure&bands=Low&bands=High');
    expect(result.current[0]).toMatchObject({ sort: 'tenure', page: 1, bands: ['Low', 'High'] });
  });

  it('removes a key set back to its default', () => {
    const { result } = at('?sort=tenure&page=4');
    act(() => result.current[1]({ sort: 'at_risk', part: null }));
    expect(window.location.search).toBe('?page=4');
  });

  it('round-trips an empty list', () => {
    const { result } = at('');
    act(() => result.current[1]({ bands: [] }));
    expect(window.location.search).toBe('?bands=');
    expect(result.current[0].bands).toEqual([]);
  });

  it('keeps query keys it does not own', () => {
    const { result } = at('?id=DEMO-0002');
    act(() => result.current[1]({ page: 2 }));
    expect(window.location.search).toBe('?id=DEMO-0002&page=2');
  });

  it('gives the address a change would lead to, without going there', () => {
    const { result } = at('?sort=tenure&page=4');
    expect(result.current[2]({ bands: ['High', 'Critical'], page: 1 })).toBe(
      '/explorer?sort=tenure&bands=High&bands=Critical',
    );
    expect(window.location.search).toBe('?sort=tenure&page=4');
  });

  it('adds a history entry by default and edits in place when asked', () => {
    const { result } = at('');
    const before = window.history.length;
    act(() => result.current[1]({ page: 2 }));
    expect(window.history.length).toBe(before + 1);
    act(() => result.current[1]({ q: 'DEMO' }, { replace: true }));
    expect(window.history.length).toBe(before + 1);
    expect(window.location.search).toBe('?page=2&q=DEMO');
  });
});

describe('a whole number that may be left empty', () => {
  const bounded = { tenure: param.optionalInt({ min: 0, max: 120 }) };

  function hook(search: string) {
    window.history.replaceState(null, '', `/explorer${search}`);
    return renderHook(() => useParams(bounded));
  }

  it('reads as null until a number is given', () => {
    expect(hook('').result.current[0].tenure).toBeNull();
  });

  it('reads the ends of its range, zero included', () => {
    expect(hook('?tenure=0').result.current[0].tenure).toBe(0);
    expect(hook('?tenure=120').result.current[0].tenure).toBe(120);
  });

  it.each([
    ['below the range', '?tenure=-1'],
    ['above the range', '?tenure=121'],
    ['fractional', '?tenure=2.5'],
    ['with trailing text', '?tenure=6x'],
    ['empty', '?tenure='],
  ])('reads a value %s as null', (_name, search) => {
    expect(hook(search).result.current[0].tenure).toBeNull();
  });

  it('writes zero, and removes the key for null', () => {
    const { result } = hook('');
    act(() => result.current[1]({ tenure: 0 }));
    expect(window.location.search).toBe('?tenure=0');
    expect(result.current[0].tenure).toBe(0);
    act(() => result.current[1]({ tenure: null }));
    expect(window.location.search).toBe('');
  });
});

describe('a number kept as it was typed', () => {
  const typed = { cost: param.decimal({ maxLength: 8 }) };

  function hook(search: string) {
    window.history.replaceState(null, '', `/simulator${search}`);
    return renderHook(() => useParams(typed));
  }

  it('reads as null when the key is left out, and as empty text when it is given empty', () => {
    expect(hook('').result.current[0].cost).toBeNull();
    expect(hook('?cost=').result.current[0].cost).toBe('');
  });

  it.each([['12.5'], ['12.'], ['.5'], ['-3'], ['-'], ['0'], ['12345678'], ['1e3'], ['12,50']])(
    'keeps %s as it is, for the page to read',
    (text) => {
      expect(hook(`?cost=${text}`).result.current[0].cost).toBe(text);
    },
  );

  it.each([
    ['a space', '%205'],
    ['a line break', '5%0A'],
    ['text over the length limit', '123456789'],
  ])('reads %s as null', (_name, text) => {
    expect(hook(`?cost=${text}`).result.current[0].cost).toBeNull();
  });

  it('writes empty text as an empty value, and removes the key for null', () => {
    const { result } = hook('');
    act(() => result.current[1]({ cost: '12.5' }));
    expect(window.location.search).toBe('?cost=12.5');
    act(() => result.current[1]({ cost: '' }));
    expect(window.location.search).toBe('?cost=');
    expect(result.current[0].cost).toBe('');
    act(() => result.current[1]({ cost: null }));
    expect(window.location.search).toBe('');
  });
});

describe('hrefTo', () => {
  const schema = {
    kind: param.optional(['a', 'b'] as const),
    low: param.optionalInt({ min: 0, max: 9 }),
    sort: param.choice(['up', 'down'] as const, 'up'),
  };

  it("writes another page's address with that page's own schema", () => {
    window.history.replaceState(null, '', '/here?mine=1');
    expect(hrefTo('/there', schema, { kind: 'b', low: 0, sort: 'down' })).toBe(
      '/there?kind=b&low=0&sort=down',
    );
  });

  it("leaves that page's defaults and the current page's keys out", () => {
    window.history.replaceState(null, '', '/here?mine=1');
    expect(hrefTo('/there', schema, { kind: null, sort: 'up' })).toBe('/there');
  });
});
