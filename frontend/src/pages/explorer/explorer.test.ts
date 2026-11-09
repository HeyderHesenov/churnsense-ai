import { choosesSomething } from '../../api/queries';
import { BAND_NAMES } from '../../lib/bands';
import {
  activeFilters,
  DEFAULT_DIRECTION,
  EXPLORER_PARAMS,
  explorerFilter,
  exportQuery,
  FILTER_DEFAULTS,
  idleReason,
  isPreset,
  listingQuery,
  neighbour,
  pageKeeping,
  PRESETS,
  presetChanges,
  RESET,
  tenureInvalid,
  tenureText,
  tenureValue,
  type ExplorerState,
} from './explorer';

const DEFAULTS: ExplorerState = {
  ...FILTER_DEFAULTS,
  sort: 'probability',
  dir: 'desc',
  page: 1,
  size: '15',
  id: '',
};

function state(changes: Partial<ExplorerState> = {}): ExplorerState {
  return { ...DEFAULTS, ...changes };
}

function preset(label: string) {
  const found = PRESETS.find((p) => p.label === label);
  if (!found) throw new Error(`no preset ${label}`);
  return found;
}

describe('the explorer’s address', () => {
  it('defaults to every band, probability first, fifteen rows', () => {
    const read = Object.fromEntries(
      Object.entries(EXPLORER_PARAMS).map(([key, p]) => [key, p.fallback]),
    );
    expect(read).toEqual(DEFAULTS);
  });
});

describe('the listing request', () => {
  it('sends no band list for every band, and always sends the page size', () => {
    expect(listingQuery(state())).toEqual({
      q: '',
      contract: null,
      internet: null,
      payment: null,
      tenure_min: null,
      tenure_max: null,
      flagged: null,
      partition: null,
      sort: 'probability',
      dir: 'desc',
      page: 1,
      page_size: 15,
    });
  });

  it('keeps an empty band choice, so the listing stays idle rather than asking for all', () => {
    expect(listingQuery(state({ bands: [] })).bands).toEqual([]);
  });

  it('maps every filter to the API’s names and types', () => {
    const query = listingQuery(
      state({
        q: 'DEMO',
        bands: ['High', 'Critical'],
        contract: 'Two year',
        internet: 'No',
        payment: 'Mailed check',
        tenure_min: 0,
        tenure_max: 6,
        flagged: 'false',
        part: 'validation',
        sort: 'id',
        dir: 'asc',
        page: 3,
        size: '50',
      }),
    );
    expect(query).toEqual({
      q: 'DEMO',
      bands: ['High', 'Critical'],
      contract: 'Two year',
      internet: 'No',
      payment: 'Mailed check',
      tenure_min: 0,
      tenure_max: 6,
      flagged: false,
      partition: 'validation',
      sort: 'id',
      dir: 'asc',
      page: 3,
      page_size: 50,
    });
    expect(listingQuery(state({ flagged: 'true' })).flagged).toBe(true);
  });

  it('exports the same customers in the same order, without a page', () => {
    const query = exportQuery(state({ bands: ['Low'], contract: 'One year', page: 4, size: '25' }));
    expect(query).toMatchObject({
      bands: ['Low'],
      contract: 'One year',
      sort: 'probability',
      dir: 'desc',
    });
    expect(query.page).toBeUndefined();
    expect(query.page_size).toBeUndefined();
  });
});

describe('when the listing has nothing to ask for', () => {
  it('names no band chosen', () => {
    expect(idleReason(state({ bands: [] }))).toBe('no-band');
  });

  it('names a tenure range whose minimum is above its maximum', () => {
    expect(idleReason(state({ tenure_min: 7, tenure_max: 6 }))).toBe('tenure');
  });

  it.each([[{}], [{ tenure_min: 6, tenure_max: 6 }], [{ tenure_min: 7 }], [{ tenure_max: 0 }]])(
    'has none for %j',
    (changes) => {
      expect(idleReason(state(changes))).toBeNull();
    },
  );
});

describe('idleness and the request', () => {
  it.each([
    [{}],
    [{ bands: [] }],
    [{ bands: ['High'] }],
    [{ tenure_min: 7, tenure_max: 6 }],
    [{ tenure_min: 6, tenure_max: 6 }],
    [{ bands: [], tenure_min: 9, tenure_max: 1 }],
    [{ tenure_min: 0 }],
  ] as const)('agree on whether %j asks for anything', (changes) => {
    const filters = state(changes);
    expect(idleReason(filters) === null).toBe(choosesSomething(explorerFilter(filters)));
  });
});

describe('an invalid tenure range', () => {
  it('is flagged whatever else is chosen, no band included', () => {
    expect(tenureInvalid(state({ tenure_min: 9, tenure_max: 3 }))).toBe(true);
    expect(tenureInvalid(state({ bands: [], tenure_min: 9, tenure_max: 3 }))).toBe(true);
  });

  it.each([[{}], [{ tenure_min: 3, tenure_max: 3 }], [{ tenure_min: 9 }], [{ tenure_max: 0 }]])(
    'is not %j',
    (changes) => {
      expect(tenureInvalid(state(changes))).toBe(false);
    },
  );
});

describe('a tenure box', () => {
  it.each([
    ['', ''],
    ['x', ''],
    ['0', '0'],
    ['00', '0'],
    ['06', '6'],
    ['9x', '9'],
    ['72', '72'],
    ['120', '120'],
    ['121', '120'],
    ['999', '120'],
    ['000000000006', '6'],
  ])('shows what %j means: %j', (typed, shown) => {
    expect(tenureText(typed)).toBe(shown);
  });

  it.each([
    ['', null],
    ['0', 0],
    ['007', 7],
    ['72', 72],
    ['120', 120],
    ['999', 120],
  ])('reads %j as %j', (text, value) => {
    expect(tenureValue(text)).toBe(value);
  });
});

describe('saved views', () => {
  it('are the artboard’s five, without a claim this page cannot back', () => {
    expect(PRESETS.map((p) => p.label)).toEqual([
      'All customers',
      'Flagged · month-to-month',
      'First six months',
      'Fiber + e-check',
      'Two-year contracts',
    ]);
  });

  it('apply their filters over the defaults, clear the search and go to the first page', () => {
    expect(presetChanges(preset('Flagged · month-to-month'))).toEqual({
      ...FILTER_DEFAULTS,
      flagged: 'true',
      contract: 'Month-to-month',
      page: 1,
    });
  });

  it('are pressed only when every filter matches', () => {
    const view = preset('Fiber + e-check');
    const applied = state({ internet: 'Fiber optic', payment: 'Electronic check' });
    expect(isPreset(applied, view)).toBe(true);
    expect(isPreset({ ...applied, q: 'DEMO' }, view)).toBe(false);
    expect(isPreset({ ...applied, bands: ['Low', 'Moderate', 'High'] }, view)).toBe(false);
    expect(isPreset({ ...applied, tenure_min: 0 }, view)).toBe(false);
    expect(isPreset(state(), preset('All customers'))).toBe(true);
    expect(isPreset(applied, preset('All customers'))).toBe(false);
  });

  it('compare bands as sets, so a repeated band is not another', () => {
    expect(
      isPreset(state({ bands: ['Low', 'Low', 'Moderate', 'High'] }), preset('All customers')),
    ).toBe(false);
  });

  it('compare bands as a set', () => {
    expect(isPreset(state({ bands: [...BAND_NAMES].reverse() }), preset('All customers'))).toBe(
      true,
    );
  });
});

describe('reset', () => {
  it('counts each filter off its default, the two tenure bounds separately', () => {
    expect(activeFilters(state())).toBe(0);
    expect(
      activeFilters(
        state({
          q: 'D',
          bands: ['Low'],
          contract: 'One year',
          internet: 'DSL',
          payment: 'Mailed check',
          tenure_min: 1,
          tenure_max: 2,
          flagged: 'true',
          part: 'test',
        }),
      ),
    ).toBe(9);
    expect(activeFilters(state({ bands: [] }))).toBe(1);
  });

  it('puts every filter back and goes to the first page, keeping sort, size and the open customer', () => {
    expect(RESET).toEqual({ ...FILTER_DEFAULTS, page: 1 });
    expect(Object.keys(RESET)).not.toEqual(expect.arrayContaining(['sort', 'dir', 'size', 'id']));
  });
});

describe('sorting', () => {
  it('starts IDs and tenure ascending, the rest descending', () => {
    expect(DEFAULT_DIRECTION).toEqual({
      id: 'asc',
      probability: 'desc',
      tenure: 'asc',
      monthly: 'desc',
      at_risk: 'desc',
    });
  });
});

describe('previous and next customer', () => {
  // 37 customers at 15 a page: pages of 15, 15 and 7.
  it('steps within a page', () => {
    expect(neighbour(3, 1, 37, 15)).toEqual({ page: 1, row: 4 });
    expect(neighbour(3, -1, 37, 15)).toEqual({ page: 1, row: 2 });
  });

  it('crosses to the next and the previous page', () => {
    expect(neighbour(14, 1, 37, 15)).toEqual({ page: 2, row: 0 });
    expect(neighbour(15, -1, 37, 15)).toEqual({ page: 1, row: 14 });
  });

  it('wraps at both ends of the list', () => {
    expect(neighbour(36, 1, 37, 15)).toEqual({ page: 1, row: 0 });
    expect(neighbour(0, -1, 37, 15)).toEqual({ page: 3, row: 6 });
  });

  it('has nowhere to go in a list of one', () => {
    expect(neighbour(0, 1, 1, 15)).toBeNull();
    expect(neighbour(0, -1, 0, 15)).toBeNull();
  });
});

describe('a new page size', () => {
  it('keeps the page that holds the reader’s place', () => {
    expect(pageKeeping(0, 25)).toBe(1);
    expect(pageKeeping(24, 25)).toBe(1);
    expect(pageKeeping(25, 25)).toBe(2);
    expect(pageKeeping(30, 15)).toBe(3);
  });
});
