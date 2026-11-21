import { act, fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { placement, TermTip } from './TermTip';

const DEFINITION =
  'How well the scores rank churners above stayers, across every cut. 1 is perfect.';

function setup() {
  render(
    <>
      <h3>
        PR-AUC <TermTip term="PR-AUC">{DEFINITION}</TermTip>
      </h3>
      <button type="button">Elsewhere</button>
    </>,
  );
  return {
    button: () => screen.getByRole('button', { name: 'About PR-AUC' }),
    tip: () => screen.queryByRole('tooltip'),
  };
}

describe('TermTip: what a screen reader meets', () => {
  it('is a button named for its term, described by the definition while it is closed', () => {
    const { button, tip } = setup();
    expect(button()).toHaveAccessibleName('About PR-AUC');
    expect(button()).toHaveAccessibleDescription(DEFINITION);
    expect(tip()).not.toBeInTheDocument();
    expect(screen.getByRole('tooltip', { hidden: true })).toHaveTextContent(DEFINITION);
  });

  it('shows the tooltip it is described by', async () => {
    const { button, tip } = setup();
    await userEvent.hover(button());
    expect(tip()).toBeVisible();
    expect(button()).toHaveAttribute('aria-describedby', tip()?.id);
  });
});

describe('TermTip: opening and closing', () => {
  it('opens on hover and closes when the pointer leaves', async () => {
    const { button, tip } = setup();
    await userEvent.hover(button());
    expect(tip()).toHaveTextContent(DEFINITION);
    await userEvent.unhover(button());
    expect(tip()).not.toBeInTheDocument();
  });

  it('stays open while the pointer is over the tooltip itself', async () => {
    const { button, tip } = setup();
    await userEvent.hover(button());
    const shown = tip();
    if (!shown) throw new Error('the tooltip did not open');
    await userEvent.hover(shown);
    expect(tip()).toBeVisible();
  });

  it('opens on keyboard focus and closes when the focus leaves', async () => {
    const { button, tip } = setup();
    await userEvent.tab();
    expect(button()).toHaveFocus();
    expect(tip()).toBeVisible();
    await userEvent.tab();
    expect(screen.getByRole('button', { name: 'Elsewhere' })).toHaveFocus();
    expect(tip()).not.toBeInTheDocument();
  });

  it('closes on Escape, the focus staying on its button', async () => {
    const { button, tip } = setup();
    await userEvent.tab();
    expect(tip()).toBeVisible();
    await userEvent.keyboard('{Escape}');
    expect(tip()).not.toBeInTheDocument();
    expect(button()).toHaveFocus();
  });

  it('closes on Escape while hovered, and opened by a press', async () => {
    const { button, tip } = setup();
    await userEvent.click(button());
    expect(tip()).toBeVisible();
    await userEvent.keyboard('{Escape}');
    expect(tip()).not.toBeInTheDocument();
  });

  it('is kept open by a press, and closed by a second', async () => {
    const { button, tip } = setup();
    await userEvent.click(button());
    await userEvent.unhover(button());
    expect(tip()).toBeVisible();
    await userEvent.click(button());
    expect(tip()).not.toBeInTheDocument();
  });

  it('toggles from the keyboard with Enter', async () => {
    const { button, tip } = setup();
    await userEvent.tab();
    await userEvent.keyboard('{Enter}');
    expect(tip()).toBeVisible();
    await userEvent.keyboard('{Enter}');
    expect(tip()).not.toBeInTheDocument();
    expect(button()).toHaveFocus();
  });

  it('opens on a tap and closes on a tap elsewhere, as on a touch screen', () => {
    const { button, tip } = setup();
    // A touch: no hover, and (on iOS) no focus; only the press.
    fireEvent.pointerEnter(button(), { pointerType: 'touch' });
    expect(tip()).not.toBeInTheDocument();
    fireEvent.pointerDown(button(), { pointerType: 'touch' });
    fireEvent.click(button());
    expect(tip()).toBeVisible();
    fireEvent.pointerDown(screen.getByRole('heading'), { pointerType: 'touch' });
    expect(tip()).not.toBeInTheDocument();
  });

  it('closes on a tap of its button a second time', () => {
    const { button, tip } = setup();
    fireEvent.click(button());
    fireEvent.click(button());
    expect(tip()).not.toBeInTheDocument();
  });

  it('stays open for a press inside it', () => {
    const { button, tip } = setup();
    fireEvent.click(button());
    const shown = tip();
    if (!shown) throw new Error('the tooltip did not open');
    fireEvent.pointerDown(shown, { pointerType: 'touch' });
    expect(tip()).toBeVisible();
  });

  it('keeps the first Escape to itself, so a drawer around it stays open', async () => {
    const outer = vi.fn();
    const onEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') outer();
    };
    document.addEventListener('keydown', onEscape);
    try {
      const { tip } = setup();
      await userEvent.tab();
      await userEvent.keyboard('{Escape}');
      expect(tip()).not.toBeInTheDocument();
      expect(outer).not.toHaveBeenCalled();
      await userEvent.keyboard('{Escape}');
      expect(outer).toHaveBeenCalledTimes(1);
    } finally {
      document.removeEventListener('keydown', onEscape);
    }
  });

  it('stays pinned through a press on its own text, which takes the focus away', () => {
    const { button, tip } = setup();
    act(() => button().focus());
    fireEvent.click(button());
    const shown = tip();
    if (!shown) throw new Error('the tooltip did not open');
    // A tap on the text: down inside, the button loses focus, up.
    fireEvent.pointerDown(shown, { pointerType: 'touch' });
    act(() => button().blur());
    fireEvent.pointerUp(shown, { pointerType: 'touch' });
    expect(tip()).toBeVisible();
  });

  it('closes a pinned tip when the keyboard leaves it, even after a press on it', async () => {
    const { button, tip } = setup();
    await userEvent.click(button());
    expect(tip()).toBeVisible();
    await userEvent.unhover(button());
    await userEvent.tab();
    expect(screen.getByRole('button', { name: 'Elsewhere' })).toHaveFocus();
    expect(tip()).not.toBeInTheDocument();
  });

  it('listens for Escape and outside presses only while open', async () => {
    const add = vi.spyOn(document, 'addEventListener');
    const { button } = setup();
    expect(add.mock.calls.filter(([type]) => type === 'keydown')).toHaveLength(0);
    await userEvent.hover(button());
    expect(add.mock.calls.filter(([type]) => type === 'keydown').length).toBeGreaterThan(0);
  });
});

describe('TermTip: placement', () => {
  const VIEW = { width: 1440, height: 900 };
  const TIP = { width: 260, height: 80 };
  const button = (left: number, top: number) => ({ left, top, bottom: top + 18, width: 18 });

  it('opens above and centred where there is room', () => {
    expect(placement(button(600, 400), TIP, VIEW)).toEqual({ side: 'above', shift: 4 });
  });

  it('opens below a button near the top of the window', () => {
    expect(placement(button(600, 40), TIP, VIEW)).toEqual({ side: 'below', shift: 4 });
  });

  it('opens above when neither side has room and above has more', () => {
    expect(
      placement(button(600, 70), { width: 260, height: 120 }, { width: 1440, height: 150 }),
    ).toMatchObject({ side: 'above' });
  });

  it('slides right of a button near the left edge, never past it', () => {
    const { shift } = placement(button(10, 400), TIP, VIEW);
    expect(shift).toBe(0);
    const near = placement(button(80, 400), TIP, VIEW).shift;
    // The tip's left edge: the button's centre less shift/8 of its width.
    expect(89 - (near / 8) * 260).toBeGreaterThanOrEqual(8);
  });

  it('slides left of a button near the right edge, never past it', () => {
    const { shift } = placement(button(1420, 400), TIP, VIEW);
    expect(shift).toBe(8);
    const near = placement(button(1330, 400), TIP, VIEW).shift;
    expect(1339 + (1 - near / 8) * 260).toBeLessThanOrEqual(1440 - 8);
  });

  it.each([16, 60, 100, 120, 140, 180, 220, 250, 290, 330, 339])(
    'fits a phone’s window with the button at %i px',
    (left) => {
      const phone = { width: 375, height: 812 };
      const tip = { width: 260, height: 80 };
      const { shift } = placement(button(left, 400), tip, phone);
      const start = left + 9 - (shift / 8) * tip.width;
      expect(start).toBeGreaterThanOrEqual(8);
      expect(start + tip.width).toBeLessThanOrEqual(phone.width - 8);
    },
  );

  it('is placed from measurements when it opens', async () => {
    vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (
      this: HTMLElement,
    ) {
      const rect =
        this.getAttribute('role') === 'tooltip'
          ? { left: 0, top: 0, width: 260, height: 80 }
          : { left: 4, top: 20, width: 18, height: 18 };
      return {
        ...rect,
        x: rect.left,
        y: rect.top,
        right: rect.left + rect.width,
        bottom: rect.top + rect.height,
        toJSON: () => rect,
      };
    });
    const { button, tip } = setup();
    await userEvent.hover(button());
    expect(tip()).toHaveAttribute('data-side', 'below');
    expect(tip()).toHaveAttribute('data-shift', '0');
  });

  it('is placed again as the page scrolls, and as the window changes size', async () => {
    let buttonTop = 400;
    vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (
      this: HTMLElement,
    ) {
      const rect =
        this.getAttribute('role') === 'tooltip'
          ? { left: 0, top: 0, width: 260, height: 80 }
          : { left: 600, top: buttonTop, width: 18, height: 18 };
      return {
        ...rect,
        x: rect.left,
        y: rect.top,
        right: rect.left + rect.width,
        bottom: rect.top + rect.height,
        toJSON: () => rect,
      };
    });
    const { button, tip } = setup();
    await userEvent.click(button());
    expect(tip()).toHaveAttribute('data-side', 'above');
    buttonTop = 20;
    act(() => {
      document.dispatchEvent(new Event('scroll'));
    });
    expect(tip()).toHaveAttribute('data-side', 'below');
    buttonTop = 400;
    act(() => {
      window.dispatchEvent(new Event('resize'));
    });
    expect(tip()).toHaveAttribute('data-side', 'above');
  });

  it('writes no style into the page', async () => {
    const { button, tip } = setup();
    await userEvent.hover(button());
    act(() => undefined);
    expect(tip()).not.toHaveAttribute('style');
    expect(button()).not.toHaveAttribute('style');
  });
});
