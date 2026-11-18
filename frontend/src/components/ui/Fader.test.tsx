import { act, fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import { cut } from '../../lib/format';
import { Fader, positionOf, typedValue } from './Fader';

/** A public demo's sweep: five published cuts, nothing in between. */
const CUTS = [0.12, 0.25, 0.36, 0.48, 0.61] as const;
/** The dataset's sweep: the whole 0.01 grid, as the API sends it. */
const GRID = Array.from({ length: 97 }, (_, i) => Number(((i + 1) / 100).toFixed(2)));

interface HarnessProps {
  values?: readonly number[];
  start?: number | null;
  defaultIndex?: number;
  disabled?: boolean;
  loading?: boolean;
  marks?: readonly number[];
  caption?: string;
}

/** The fader as a page holds it; every value it is given lands in ``chosen``. */
function setup({
  values = CUTS,
  start = 2,
  defaultIndex,
  disabled,
  loading,
  marks,
  caption,
}: HarnessProps = {}) {
  const chosen: number[] = [];
  function Harness() {
    const [index, setIndex] = useState<number | null>(start);
    return (
      <>
        <Fader
          label="Decision threshold"
          values={values}
          index={index}
          format={cut}
          onChange={(next) => {
            chosen.push(values[next] as number);
            setIndex(next);
          }}
          defaultIndex={defaultIndex}
          {...(marks ? { marks } : {})}
          {...(caption ? { caption } : {})}
          {...(disabled ? { disabled } : {})}
          {...(loading ? { loading } : {})}
        />
        <button type="button">Elsewhere</button>
      </>
    );
  }
  render(<Harness />);
  return {
    chosen,
    range: () => screen.getByRole('slider', { name: 'Decision threshold' }),
    field: () => screen.getByRole('textbox', { name: 'Decision threshold value' }),
    lower: () => screen.getByRole('button', { name: 'Lower Decision threshold' }),
    raise: () => screen.getByRole('button', { name: 'Raise Decision threshold' }),
  };
}

async function keys(control: HTMLElement, sequence: string) {
  act(() => control.focus());
  await userEvent.keyboard(sequence);
}

describe('typedValue', () => {
  it.each([
    ['0.36', 0.36],
    ['0,36', 0.36],
    [' 0,36 ', 0.36],
    ['.36', 0.36],
    [',36', 0.36],
    ['1', 1],
    ['-0.5', -0.5],
  ])('reads %j as %s', (text, expected) => {
    expect(typedValue(text)).toBe(expected);
  });

  it.each(['', ' ', 'abc', '0.3.6', '0,3,6', '0,3.6', '1e-1', '0x10', '36%', 'Infinity', '-', '.'])(
    'refuses %j',
    (text) => {
      expect(typedValue(text)).toBeNull();
    },
  );
});

describe('positionOf', () => {
  it('finds a value only when it is one of the values', () => {
    expect(positionOf(CUTS, 0.36)).toBe(2);
    expect(positionOf(CUTS, 0.1 + 0.26)).toBe(2);
    expect(positionOf(CUTS, 0.355)).toBeNull();
    expect(positionOf(CUTS, 0.37)).toBeNull();
    expect(positionOf(CUTS, 0)).toBeNull();
    expect(positionOf([], 0.36)).toBeNull();
  });
});

describe('Fader: the range', () => {
  it('is a slider over the positions of the values, read out as the value', () => {
    const { range } = setup();
    expect(range()).toHaveAttribute('type', 'range');
    expect(range()).toHaveAttribute('min', '0');
    expect(range()).toHaveAttribute('max', '4');
    expect(range()).toHaveAttribute('step', '1');
    expect(range()).toHaveValue('2');
    expect(range()).toHaveAttribute('aria-valuetext', '0.36');
  });

  it('gives only values from the list when dragged to any position', () => {
    const { range, chosen } = setup();
    for (const position of ['0', '4', '1', '3']) {
      fireEvent.change(range(), { target: { value: position } });
    }
    expect(chosen).toEqual([0.12, 0.61, 0.25, 0.48]);
    expect(chosen.every((value) => (CUTS as readonly number[]).includes(value))).toBe(true);
  });

  it('steps one value with an arrow key', async () => {
    const { range, chosen } = setup();
    await keys(range(), '{ArrowRight}');
    expect(chosen).toEqual([0.48]);
    await keys(range(), '{ArrowLeft}{ArrowLeft}');
    expect(chosen).toEqual([0.48, 0.36, 0.25]);
    await keys(range(), '{ArrowUp}{ArrowDown}');
    expect(chosen).toEqual([0.48, 0.36, 0.25, 0.36, 0.25]);
    expect(range()).toHaveAttribute('aria-valuetext', '0.25');
  });

  it('steps ten values with Shift and an arrow, and stops at the ends', async () => {
    const { range, chosen } = setup({ values: GRID, start: 35 });
    await keys(range(), '{Shift>}{ArrowRight}{/Shift}');
    expect(chosen).toEqual([0.46]);
    await keys(range(), '{Shift>}{ArrowLeft}{ArrowLeft}{/Shift}');
    expect(chosen).toEqual([0.46, 0.36, 0.26]);
    await keys(range(), '{Shift>}{ArrowLeft}{ArrowLeft}{ArrowLeft}{/Shift}');
    expect(chosen).toEqual([0.46, 0.36, 0.26, 0.16, 0.06, 0.01]);
    await keys(range(), '{Shift>}{ArrowDown}{/Shift}');
    expect(chosen).toHaveLength(6);
  });

  it('steps ten values with Page Up and Page Down', async () => {
    const { range, chosen } = setup({ values: GRID, start: 35 });
    await keys(range(), '{PageUp}{PageDown}{PageDown}');
    expect(chosen).toEqual([0.46, 0.36, 0.26]);
  });

  it('jumps to the first and last values with Home and End', async () => {
    const { range, chosen } = setup();
    await keys(range(), '{End}');
    expect(chosen).toEqual([0.61]);
    await keys(range(), '{End}');
    expect(chosen).toEqual([0.61]);
    await keys(range(), '{Home}');
    expect(chosen).toEqual([0.61, 0.12]);
  });

  it('stays on the scale at either end', async () => {
    const { range, chosen } = setup({ start: 0 });
    await keys(range(), '{ArrowLeft}{Shift>}{ArrowLeft}{/Shift}{PageDown}');
    expect(chosen).toEqual([]);
    expect(range()).toHaveValue('0');
  });

  it('only ever moves to a value from the list, whatever the key', async () => {
    const { range, chosen } = setup({ values: GRID, start: 50 });
    await keys(
      range(),
      '{ArrowRight}{Shift>}{ArrowRight}{/Shift}{PageDown}{ArrowLeft}{End}{Home}{Shift>}{ArrowUp}{/Shift}',
    );
    expect(chosen.length).toBeGreaterThan(5);
    for (const value of chosen) expect(GRID).toContain(value);
  });

  it.each([
    ['Alt', 'ArrowLeft', { altKey: true }],
    ['Cmd', 'ArrowRight', { metaKey: true }],
    ['Ctrl', 'Home', { ctrlKey: true }],
  ])('leaves %s with a key to the browser', (_name, key, modifier) => {
    const { range, chosen } = setup();
    const event = new KeyboardEvent('keydown', {
      key,
      bubbles: true,
      cancelable: true,
      ...modifier,
    });
    act(() => {
      range().dispatchEvent(event);
    });
    expect(event.defaultPrevented).toBe(false);
    expect(chosen).toEqual([]);
  });

  it('has its caption as its description', () => {
    const { range } = setup({ caption: 'Customers at or above it get an offer.' });
    expect(range()).toHaveAccessibleDescription('Customers at or above it get an offer.');
  });
});

describe('Fader: the stepper', () => {
  it('steps one value with its buttons', async () => {
    const { lower, raise, chosen } = setup();
    await userEvent.click(raise());
    await userEvent.click(lower());
    await userEvent.click(lower());
    expect(chosen).toEqual([0.48, 0.36, 0.25]);
  });

  it('says its button is unavailable at each end, and does nothing there', async () => {
    const { lower, raise, chosen } = setup({ start: 0 });
    expect(lower()).toHaveAttribute('aria-disabled', 'true');
    expect(raise()).not.toHaveAttribute('aria-disabled');
    await userEvent.click(lower());
    expect(chosen).toEqual([]);
    await keys(screen.getByRole('slider'), '{End}');
    expect(raise()).toHaveAttribute('aria-disabled', 'true');
    expect(lower()).not.toHaveAttribute('aria-disabled');
  });

  it('keeps the focus on a button that reaches the end', async () => {
    const { raise } = setup({ start: 3 });
    await userEvent.click(raise());
    expect(raise()).toHaveFocus();
    expect(raise()).toHaveAttribute('aria-disabled', 'true');
  });

  it('shows the chosen value in its field', () => {
    const { field } = setup();
    expect(field()).toHaveValue('0.36');
    expect(field()).toHaveAttribute('inputmode', 'decimal');
  });

  it.each([
    ['0.48', 0.48],
    ['0,48', 0.48],
    ['.61', 0.61],
    ['0.120', 0.12],
  ])('takes %j, typed and entered, as that value', async (typed, expected) => {
    const { field, chosen } = setup();
    await userEvent.clear(field());
    await userEvent.type(field(), `${typed}{Enter}`);
    expect(chosen).toEqual([expected]);
    expect(field()).toHaveValue(cut(expected));
    expect(field()).toHaveAttribute('aria-invalid', 'false');
  });

  it('takes a typed value when the field is left', async () => {
    const { field, chosen } = setup();
    await userEvent.clear(field());
    await userEvent.type(field(), '0,25');
    await userEvent.tab();
    expect(chosen).toEqual([0.25]);
  });

  it('refuses a value between two published values, naming the nearest, and never rounds to one', async () => {
    const { field, chosen, range } = setup();
    await userEvent.clear(field());
    await userEvent.type(field(), '0,355{Enter}');
    expect(chosen).toEqual([]);
    expect(range()).toHaveValue('2');
    expect(field()).toHaveAttribute('aria-invalid', 'true');
    expect(field()).toHaveValue('0,355');
    expect(field()).toHaveAccessibleDescription(
      '0,355 is not one of the values on this scale. The nearest are 0.25 and 0.36.',
    );
  });

  it.each([
    ['below the scale', '0.05', '0.05 is outside the scale. The scale runs from 0.12 to 0.61.'],
    ['above the scale', '0.9', '0.9 is outside the scale. The scale runs from 0.12 to 0.61.'],
    ['not a number', 'abc', '“abc” is not a number. The scale runs from 0.12 to 0.61.'],
    ['two separators', '0,3.6', '“0,3.6” is not a number. The scale runs from 0.12 to 0.61.'],
  ])('refuses a value %s, and says why', async (_name, typed, message) => {
    const { field, chosen } = setup();
    await userEvent.clear(field());
    await userEvent.type(field(), `${typed}{Enter}`);
    expect(chosen).toEqual([]);
    expect(field()).toHaveAttribute('aria-invalid', 'true');
    expect(screen.getByText(message)).toBeInTheDocument();
  });

  it('refuses a 0.01 step on a sweep published at a few cuts', async () => {
    const { field, chosen } = setup();
    await userEvent.clear(field());
    await userEvent.type(field(), '0.37{Enter}');
    expect(chosen).toEqual([]);
    expect(field()).toHaveAttribute('aria-invalid', 'true');
  });

  it('puts the error in a polite live region that is there before it', async () => {
    const { field } = setup();
    const region = document.querySelector('[aria-live="polite"]');
    expect(region).toBeEmptyDOMElement();
    await userEvent.clear(field());
    await userEvent.type(field(), '0.3{Enter}');
    expect(region).toHaveTextContent('0.3 is not one of the values on this scale.');
  });

  it('puts the shown value back on Escape, and clears the error', async () => {
    const { field, chosen } = setup();
    await userEvent.clear(field());
    await userEvent.type(field(), '0.3{Enter}');
    await userEvent.keyboard('{Escape}');
    expect(field()).toHaveValue('0.36');
    expect(field()).toHaveAttribute('aria-invalid', 'false');
    expect(document.querySelector('[aria-live="polite"]')).toBeEmptyDOMElement();
    expect(chosen).toEqual([]);
  });

  it('clears a refused value once the slider moves', async () => {
    const { field, range } = setup();
    await userEvent.clear(field());
    await userEvent.type(field(), '0.3{Enter}');
    await keys(range(), '{ArrowRight}');
    expect(field()).toHaveValue('0.48');
    expect(field()).toHaveAttribute('aria-invalid', 'false');
  });

  it('has a name of its own, apart from the slider’s', () => {
    const { field, range } = setup();
    expect(field()).toHaveAccessibleName('Decision threshold value');
    expect(range()).toHaveAccessibleName('Decision threshold');
    expect(screen.queryByRole('group')).not.toBeInTheDocument();
  });

  it('drops a refused value when the value is set from outside', async () => {
    const values = CUTS;
    function Outside() {
      const [index, setIndex] = useState(2);
      return (
        <>
          <Fader label="Cut" values={values} index={index} onChange={setIndex} format={cut} />
          <button type="button" onClick={() => setIndex(4)}>
            Preset
          </button>
        </>
      );
    }
    render(<Outside />);
    const field = screen.getByRole('textbox', { name: 'Cut value' });
    await userEvent.clear(field);
    await userEvent.type(field, '0.3{Enter}');
    expect(field).toHaveAttribute('aria-invalid', 'true');
    await userEvent.click(screen.getByRole('button', { name: 'Preset' }));
    expect(field).toHaveValue('0.61');
    expect(field).toHaveAttribute('aria-invalid', 'false');
  });

  it('keeps what is being typed when the page draws the same scale again', async () => {
    function Redraw() {
      const [, setTick] = useState(0);
      return (
        <>
          <Fader label="Cut" values={[...CUTS]} index={2} onChange={() => undefined} format={cut} />
          <button type="button" onClick={() => setTick((t) => t + 1)}>
            Redraw
          </button>
        </>
      );
    }
    render(<Redraw />);
    const field = screen.getByRole('textbox', { name: 'Cut value' });
    await userEvent.clear(field);
    await userEvent.type(field, '0.4');
    fireEvent.click(screen.getByRole('button', { name: 'Redraw' }));
    expect(field).toHaveValue('0.4');
  });

  it('drops what was typed when a load starts', () => {
    function Loading({ loading }: { loading: boolean }) {
      return (
        <Fader
          label="Cut"
          values={CUTS}
          index={2}
          onChange={() => undefined}
          format={cut}
          loading={loading}
        />
      );
    }
    const { rerender } = render(<Loading loading={false} />);
    const field = screen.getByRole('textbox', { name: 'Cut value' });
    fireEvent.change(field, { target: { value: '0.4' } });
    expect(field).toHaveValue('0.4');
    rerender(<Loading loading />);
    expect(field).toHaveValue('0.36');
  });

  it('does nothing when the typed value is the one already chosen', async () => {
    const { field, chosen } = setup();
    await userEvent.clear(field());
    await userEvent.type(field(), '0,36{Enter}');
    expect(chosen).toEqual([]);
    expect(field()).toHaveValue('0.36');
  });
});

describe('Fader: the default', () => {
  it('shows no reset away from nothing: at the default there is none', () => {
    setup({ defaultIndex: 2 });
    expect(screen.queryByRole('button', { name: /^Reset/ })).not.toBeInTheDocument();
  });

  it('offers a reset to the default once moved, and goes back with focus kept on the fader', async () => {
    const { range, chosen } = setup({ defaultIndex: 2 });
    await keys(range(), '{End}');
    const reset = screen.getByRole('button', { name: 'Reset Decision threshold to 0.36' });
    expect(reset).toHaveTextContent('↺Reset Decision threshold to 0.36');
    await userEvent.click(reset);
    expect(chosen).toEqual([0.61, 0.36]);
    expect(screen.queryByRole('button', { name: /^Reset/ })).not.toBeInTheDocument();
    expect(range()).toHaveFocus();
  });
});

describe('Fader: unavailable', () => {
  it('disables every control when disabled', () => {
    const { range, field, lower, raise, chosen } = setup({ disabled: true, defaultIndex: 0 });
    for (const control of [range(), field(), lower(), raise()]) expect(control).toBeDisabled();
    expect(screen.queryByRole('button', { name: /^Reset/ })).not.toBeInTheDocument();
    fireEvent.change(range(), { target: { value: '4' } });
    expect(chosen).toEqual([]);
  });

  it('disables itself with no values to choose from, and shows no value', () => {
    const { range, field } = setup({ values: [], start: null });
    expect(range()).toBeDisabled();
    expect(range()).toHaveAttribute('aria-valuetext', 'no value');
    expect(field()).toHaveValue('');
    expect(field()).toHaveAttribute('placeholder', '—');
  });

  it('while loading, says so, keeps its place and focus, and does nothing', async () => {
    const { range, field, lower, raise, chosen } = setup({ loading: true, defaultIndex: 0 });
    expect(range().closest('[aria-busy]')).toHaveAttribute('aria-busy', 'true');
    expect(screen.getByRole('button', { name: /^Reset/ })).toHaveAttribute('aria-disabled', 'true');
    for (const control of [range(), field(), lower(), raise()]) {
      expect(control).not.toBeDisabled();
      expect(control).toHaveAttribute('aria-disabled', 'true');
    }
    await keys(range(), '{ArrowRight}{End}{Shift>}{ArrowLeft}{/Shift}');
    expect(range()).toHaveFocus();
    fireEvent.change(range(), { target: { value: '4' } });
    await userEvent.click(raise());
    await userEvent.click(lower());
    await userEvent.type(field(), '1{Enter}');
    await userEvent.click(screen.getByRole('button', { name: /^Reset/ }));
    expect(chosen).toEqual([]);
    expect(range()).toHaveValue('2');
    expect(field()).toHaveValue('0.36');
  });

  it('shows no value while none is chosen', () => {
    const { range, field } = setup({ start: null });
    expect(range()).toHaveAttribute('aria-valuetext', 'no value');
    expect(field()).toHaveValue('');
  });

  it.each([
    ['an arrow', '{ArrowRight}', 0.12],
    ['Shift and an arrow', '{Shift>}{ArrowRight}{/Shift}', 0.12],
    ['Home', '{Home}', 0.12],
    ['End', '{End}', 0.61],
  ])('chooses a first value from none with %s', async (_name, key, expected) => {
    const { range, chosen } = setup({ start: null });
    await keys(range(), key);
    expect(chosen).toEqual([expected]);
  });

  it.each(['Lower', 'Raise'])('chooses the first value from none with %s', async (name) => {
    const { chosen } = setup({ start: null });
    const button = screen.getByRole('button', { name: `${name} Decision threshold` });
    expect(button).not.toHaveAttribute('aria-disabled');
    await userEvent.click(button);
    expect(chosen).toEqual([0.12]);
  });

  it('chooses a typed value from none, and a dragged one', async () => {
    const { range, field, chosen } = setup({ start: null });
    await userEvent.type(field(), '0,48{Enter}');
    expect(chosen).toEqual([0.48]);
    fireEvent.change(range(), { target: { value: '0' } });
    expect(chosen).toEqual([0.48, 0.12]);
  });

  it('offers the default from none', async () => {
    const { chosen } = setup({ start: null, defaultIndex: 2 });
    await userEvent.click(screen.getByRole('button', { name: 'Reset Decision threshold to 0.36' }));
    expect(chosen).toEqual([0.36]);
  });
});

describe('Fader: the drawing', () => {
  it('is hidden from assistive technology, and holds no control', () => {
    setup({ marks: [0, 2, 4] });
    const svg = document.querySelector('svg');
    expect(svg).toHaveAttribute('aria-hidden', 'true');
    expect(svg?.querySelectorAll('button, input, a')).toHaveLength(0);
  });

  it('labels the marked values, read from the list and nowhere else', () => {
    setup({ marks: [0, 2, 4, 9, -1] });
    const texts = Array.from(document.querySelectorAll('svg text')).map((t) => t.textContent);
    // Three marks (9 and -1 are off the scale), then the value bubble.
    expect(texts).toEqual(['0.12', '0.36', '0.61', '0.36']);
  });

  it('places the fill and the ticks at the value’s position along the rail', () => {
    setup({ values: CUTS, start: 1, defaultIndex: 3 });
    const fill = document.querySelector('svg rect[fill^="url(#"]');
    expect(fill).toHaveAttribute('width', '25%');
    const ticks = Array.from(document.querySelectorAll('svg line')).map((line) =>
      line.getAttribute('x1'),
    );
    expect(ticks).toEqual(['0%', '25%', '50%', '75%', '100%', '75%']);
  });

  it('draws no minor tick for each of a long list', () => {
    setup({ values: GRID, start: 35 });
    expect(document.querySelectorAll('svg line')).toHaveLength(0);
  });

  it('refers to its own gradient by a name a url() can hold', () => {
    setup();
    const fill = document.querySelector('svg rect[fill^="url(#"]');
    const name = /^url\(#([A-Za-z0-9]+)\)$/.exec(fill?.getAttribute('fill') ?? '')?.[1];
    expect(name).toBeDefined();
    expect(document.getElementById(name ?? '')?.tagName).toBe('linearGradient');
  });
});
