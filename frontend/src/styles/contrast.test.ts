/**
 * The new design v4 text colours, read from the tokens themselves, each on the
 * ground it is drawn on: at least 4.5:1 (WCAG 1.4.3). A translucent ground is
 * laid over the one under it first. The figures the design gives are checked
 * here because no page shows these controls yet for the end-to-end axe pass.
 */
import tokens from './tokens.css?raw';

/** Every ``--name: value;`` in the file, by name. */
const TOKENS = new Map(
  Array.from(tokens.matchAll(/--([\w-]+):\s*([^;]+);/g), ([, name, value]) => [
    name ?? '',
    (value ?? '').trim(),
  ]),
);

it('defines each token once, so the colours checked here are the ones drawn', () => {
  const names = Array.from(tokens.matchAll(/--([\w-]+):/g), ([, name]) => name);
  expect(names.length).toBeGreaterThan(100);
  expect(new Set(names).size).toBe(names.length);
});

function token(name: string): string {
  const value = TOKENS.get(name);
  if (value === undefined) throw new Error(`no token --${name}`);
  return value;
}

type Rgba = [number, number, number, number];

function colour(value: string): Rgba {
  const hex = /^#([0-9a-f]{6})$/i.exec(value)?.[1];
  if (hex) return [0, 2, 4].map((i) => parseInt(hex.slice(i, i + 2), 16)).concat(1) as Rgba;
  const rgba = /^rgba\((\d+),\s*(\d+),\s*(\d+),\s*([\d.]+)\)$/.exec(value);
  if (rgba) return [Number(rgba[1]), Number(rgba[2]), Number(rgba[3]), Number(rgba[4])];
  throw new Error(`not a colour: ${value}`);
}

/** ``top`` laid over the opaque ``under``. */
function over(top: Rgba, under: Rgba): Rgba {
  const a = top[3];
  return [0, 1, 2].map((i) => (top[i] ?? 0) * a + (under[i] ?? 0) * (1 - a)).concat(1) as Rgba;
}

function luminance([r, g, b]: Rgba): number {
  const linear = (c: number) => {
    const s = c / 255;
    return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * linear(r) + 0.7152 * linear(g) + 0.0722 * linear(b);
}

function contrast(ink: Rgba, ground: Rgba): number {
  const [hi, lo] = [luminance(ink), luminance(ground)].sort((a, b) => b - a) as [number, number];
  return (hi + 0.05) / (lo + 0.05);
}

/** A ground of layered tokens, the bottom one opaque: ``['tab-selected', 'tabs-ground', 'ground']``. */
function ground(layers: readonly string[]): Rgba {
  const [bottom, ...rest] = [...layers].reverse().map((name) => colour(token(name)));
  if (!bottom) throw new Error('no ground');
  return rest.reduce((under, top) => over(top, under), bottom);
}

const strip = ['tabs-ground', 'ground'];

it.each([
  ['an unselected tab’s label', 'muted', strip],
  ['an unselected tab’s hint', 'placeholder', strip],
  ['the selected tab’s label', 'ink', ['tab-selected', ...strip]],
  ['the selected tab’s hint', 'ink-2', ['tab-selected', ...strip]],
  ['the selected tab’s number', 'on-accent', ['accent']],
  ['a tab’s count', 'ink-2', strip],
  ['a term tip’s text', 'tip-ink', ['tip-ground']],
  ['a term tip’s "?"', 'muted', ['surface']],
  ['the fader’s value bubble', 'tip-ink', ['tip-ground']],
  ['the fader’s stepper value', 'ink', ['stepper-ground']],
  ['the fader’s label', 'ink-2', ['surface']],
  ['the fader’s mark labels and caption', 'muted', ['surface']],
  ['the fader’s refusal', 'critical-ink', ['surface']],
  ['an info note’s text', 'info-text', ['info-soft', 'surface']],
  ['an info note’s point', 'ink', ['info-soft', 'surface']],
  ['a disclosure’s summary', 'ink', ['well']],
  ['a disclosure’s marker', 'accent-text', ['well']],
  ['a disclosure’s body', 'ink-2', ['well']],
])('%s is at least 4.5:1 on its ground', (_name, ink, layers) => {
  expect(contrast(colour(token(ink)), ground(layers))).toBeGreaterThanOrEqual(4.5);
});
