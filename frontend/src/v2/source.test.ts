/**
 * The V2 Preview's source, read as text: what every file of it must and must
 * not hold, whichever page it is on.
 */
import { CAUSAL } from '../test/wording';
import { V2_API_PATHS } from './api';
import * as copy from './copy';
import { CANONICAL_FIELDS, REQUIRED, STATUSES } from './fields';
import { V2_ROUTES } from './routes';

const sources = Object.entries(
  import.meta.glob<string>(['./**/*.tsx', './**/*.ts'], {
    query: '?raw',
    import: 'default',
    eager: true,
  }),
).filter(([path]) => !path.includes('.test.') && !path.startsWith('./test/'));

/** ``source`` without its comments: what runs, not what explains it. */
function code(source: string): string {
  return source.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '');
}

describe('the V2 source', () => {
  it('has files to check', () => {
    expect(sources.length).toBeGreaterThan(15);
  });

  it.each(sources)('%s sets no style attribute and writes no HTML', (_path, source) => {
    expect(source).not.toMatch(/\bstyle=/);
    // ``style: 'currency'`` is an Intl.NumberFormat option, not a style.
    expect(source.replaceAll("style: 'currency'", '')).not.toMatch(
      /\bstyle\s*:|\.style\b|setAttribute\(\s*['"]style/,
    );
    expect(source).not.toMatch(/\bcssVars\b/);
    expect(source).not.toMatch(/dangerouslySetInnerHTML|innerHTML|\beval\(/);
  });

  it.each(sources)('%s makes no causal claim', (_path, source) => {
    expect(source).not.toMatch(CAUSAL);
  });

  it.each(sources)('%s asks for no M3 or M4 contract, and stores nothing', (_path, source) => {
    expect(code(source)).not.toMatch(/experiment|outcome|playbook|action_|retention_metric/i);
    expect(source).not.toMatch(/localStorage|sessionStorage|indexedDB|document\.cookie/);
  });

  it.each(sources)('%s reads no uploaded file in the browser', (_path, source) => {
    expect(source).not.toMatch(/FileReader|\.text\(\)|\.arrayBuffer\(\)|\.stream\(\)/);
  });

  it('names only its own pages and the M1/M2 routes under /v2', () => {
    const pages = new Set(V2_ROUTES.map((route) => route.path));
    const api = new Set<string>(V2_API_PATHS);
    for (const [path, source] of sources) {
      for (const [literal] of source.matchAll(/['"`]\/v2[^'"`$?]*/g)) {
        const target = literal.slice(1);
        expect(pages.has(target) || api.has(target), `${path}: ${target}`).toBe(true);
      }
    }
  });
});

describe('the V2 copy', () => {
  const words = JSON.stringify(copy);

  it('makes no causal claim and no forecast of revenue', () => {
    expect(words).not.toMatch(CAUSAL);
    expect(copy.EXPOSED_MRR).toMatch(/not a forecast of lost revenue/);
    expect(copy.EXPOSED_MRR).toMatch(/not revenue saved or recoverable/);
  });

  it('keeps the two tools apart, and the data synthetic', () => {
    expect(copy.BOUNDARY.body).toMatch(/not added to the portfolio demo/);
    expect(copy.NOT_SAVED.body).toMatch(/Nothing is imported, stored or added to the portfolio/);
    expect(copy.SYNTHETIC.body).toMatch(/not your file and not real customers/);
    expect(copy.SYNTHETIC.body).toMatch(/not a model/);
  });
});

describe('the contract the Data Hub offers', () => {
  it('is the generated schema’s fields, required ones and statuses', () => {
    expect(CANONICAL_FIELDS).toHaveLength(20);
    expect(REQUIRED).toEqual(['account_external_id', 'as_of_date', 'status', 'mrr', 'currency']);
    expect(STATUSES).toEqual(['trialing', 'active', 'past_due', 'paused', 'canceled']);
  });
});
