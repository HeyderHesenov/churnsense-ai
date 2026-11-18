import { act, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { param, useParams } from '../../app/params';
import { entriesAdded } from '../../test/history';
import { PageTabs, pageTabs, scrolledTo, usePageTab, type TabCount } from './PageTabs';

const SECTIONS = pageTabs([
  { value: 'summary', label: 'Summary', hint: 'The book at a glance' },
  { value: 'risk', label: 'Risk distribution', hint: 'Where customers sit' },
  { value: 'drivers', label: 'Drivers', hint: 'What risk goes with' },
  { value: 'queue', label: 'Retention queue', hint: 'Who to contact first' },
]);

/** A page's other params, which the tabs must leave as they are. */
const OTHERS = { id: param.text({ pattern: /^[A-Za-z0-9-]*$/, maxLength: 32 }) };

function Page({
  hints,
  counts,
}: {
  hints?: Partial<Record<'summary' | 'risk' | 'drivers' | 'queue', string>>;
  counts?: Partial<Record<'summary' | 'risk' | 'drivers' | 'queue', TabCount>>;
}) {
  const [tab, setTab] = usePageTab(SECTIONS);
  const [{ id }] = useParams(OTHERS);
  return (
    <PageTabs
      def={SECTIONS}
      label="Overview sections"
      value={tab}
      onChange={setTab}
      {...(hints ? { hints } : {})}
      {...(counts ? { counts } : {})}
    >
      <p>
        Showing {tab} for {id || 'no one'}
      </p>
    </PageTabs>
  );
}

function at(search: string) {
  window.history.replaceState(null, '', `/${search}`);
  return render(<Page />);
}

const tab = (name: RegExp) => screen.getByRole('tab', { name });

describe('PageTabs: what a reader meets', () => {
  it('is a named tab list whose tabs are named by their label and hint, the number left out', () => {
    at('');
    expect(screen.getByRole('tablist', { name: 'Overview sections' })).toBeInTheDocument();
    expect(screen.getAllByRole('tab').map((t) => t.textContent)).toEqual([
      '1Summary The book at a glance',
      '2Risk distribution Where customers sit',
      '3Drivers What risk goes with',
      '4Retention queue Who to contact first',
    ]);
    expect(screen.getByRole('tab', { name: 'Summary The book at a glance' })).toBeInTheDocument();
  });

  it('shows the first tab for an address without one, as the selected tab and the panel', () => {
    at('');
    expect(tab(/^Summary/)).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByRole('tabpanel', { name: /^Summary/ })).toHaveTextContent('Showing summary');
  });

  it('puts only the selected tab in the tab order, and ties it to the panel', () => {
    at('?tab=drivers');
    const selected = tab(/^Drivers/);
    expect(selected).toHaveAttribute('tabindex', '0');
    for (const other of screen.getAllByRole('tab').filter((t) => t !== selected)) {
      expect(other).toHaveAttribute('tabindex', '-1');
      expect(other).toHaveAttribute('aria-selected', 'false');
    }
    const panel = screen.getByRole('tabpanel');
    expect(selected).toHaveAttribute('aria-controls', panel.id);
    expect(panel).toHaveAccessibleName(/^Drivers/);
  });

  it('can say a figure beside a tab, its noun read but not shown', () => {
    window.history.replaceState(null, '', '/');
    render(<Page counts={{ queue: { value: '405', noun: 'customers' } }} />);
    const queue = tab(/^Retention queue/);
    expect(queue).toHaveAccessibleName('Retention queue Who to contact first 405 customers');
    expect(screen.getByText('customers')).toHaveClass('sr');
  });

  it('can change a hint with the page, such as how many are pinned', () => {
    window.history.replaceState(null, '', '/');
    render(<Page hints={{ drivers: '2 pinned' }} />);
    expect(tab(/^Drivers/)).toHaveAccessibleName('Drivers 2 pinned');
  });
});

describe('PageTabs: the address', () => {
  it.each([
    ['?tab=risk', /^Risk distribution/],
    ['?tab=queue', /^Retention queue/],
  ])('opens on the tab %s names', (search, name) => {
    at(search);
    expect(tab(name)).toHaveAttribute('aria-selected', 'true');
  });

  it.each([
    ['an unknown tab', '?tab=settings'],
    ['an empty tab', '?tab='],
    ['a label instead of a value', '?tab=Drivers'],
    ['markup', '?tab=%3Cimg%20src%3Dx%3E'],
    ['a prototype key', '?tab=__proto__'],
    ['a constructor key', '?tab=constructor'],
  ])('falls back to the first tab for %s', (_name, search) => {
    at(search);
    expect(tab(/^Summary/)).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByRole('tabpanel')).toHaveTextContent('Showing summary');
  });

  it('reads the first of a repeated tab', () => {
    at('?tab=drivers&tab=queue');
    expect(tab(/^Drivers/)).toHaveAttribute('aria-selected', 'true');
  });

  it('writes the chosen tab, and keeps every other key in the query', async () => {
    at('?id=DEMO-0042&q=fiber');
    await userEvent.click(tab(/^Drivers/));
    const search = new URLSearchParams(window.location.search);
    expect(search.get('tab')).toBe('drivers');
    expect(search.get('id')).toBe('DEMO-0042');
    expect(search.get('q')).toBe('fiber');
    expect(window.location.pathname).toBe('/');
    expect(screen.getByRole('tabpanel')).toHaveTextContent('Showing drivers for DEMO-0042');
  });

  it('leaves the first tab out of the address, the other keys still there', async () => {
    at('?tab=queue&id=DEMO-0042');
    await userEvent.click(tab(/^Summary/));
    expect(window.location.search).toBe('?id=DEMO-0042');
  });

  it('adds one history entry for each change, and none for the tab already chosen', async () => {
    at('');
    const added = entriesAdded();
    await userEvent.click(tab(/^Summary/));
    expect(added()).toBe(0);
    await userEvent.click(tab(/^Risk distribution/));
    await userEvent.click(tab(/^Drivers/));
    expect(added()).toBe(2);
    await userEvent.keyboard('{Home}');
    expect(added()).toBe(3);
    await userEvent.keyboard('{Home}');
    expect(added()).toBe(3);
  });

  it('adds no entry for choosing the tab an unknown address already shows', async () => {
    // Back would otherwise be a step that changes nothing on screen.
    at('?tab=settings&id=DEMO-0042');
    const added = entriesAdded();
    await userEvent.click(tab(/^Summary/));
    expect(added()).toBe(0);
    expect(window.location.search).toBe('?tab=settings&id=DEMO-0042');
  });

  it('follows Back and Forward', async () => {
    at('?id=DEMO-0042');
    await userEvent.click(tab(/^Risk distribution/));
    await userEvent.click(tab(/^Retention queue/));
    act(() => window.history.back());
    await waitFor(() => expect(tab(/^Risk distribution/)).toHaveAttribute('aria-selected', 'true'));
    act(() => window.history.back());
    await waitFor(() => expect(tab(/^Summary/)).toHaveAttribute('aria-selected', 'true'));
    expect(screen.getByRole('tabpanel')).toHaveTextContent('Showing summary for DEMO-0042');
    act(() => window.history.forward());
    await waitFor(() => expect(tab(/^Risk distribution/)).toHaveAttribute('aria-selected', 'true'));
    expect(window.location.search).toBe('?id=DEMO-0042&tab=risk');
  });
});

describe('PageTabs: the keyboard', () => {
  it('moves and chooses with the arrow keys, wrapping at the ends', async () => {
    at('');
    act(() => tab(/^Summary/).focus());
    await userEvent.keyboard('{ArrowRight}');
    expect(tab(/^Risk distribution/)).toHaveFocus();
    expect(tab(/^Risk distribution/)).toHaveAttribute('aria-selected', 'true');
    expect(new URLSearchParams(window.location.search).get('tab')).toBe('risk');
    await userEvent.keyboard('{ArrowLeft}{ArrowLeft}');
    expect(tab(/^Retention queue/)).toHaveFocus();
    expect(tab(/^Retention queue/)).toHaveAttribute('aria-selected', 'true');
    await userEvent.keyboard('{ArrowRight}');
    expect(tab(/^Summary/)).toHaveFocus();
  });

  it('jumps with Home and End', async () => {
    at('?tab=risk');
    act(() => tab(/^Risk distribution/).focus());
    await userEvent.keyboard('{End}');
    expect(tab(/^Retention queue/)).toHaveFocus();
    expect(tab(/^Retention queue/)).toHaveAttribute('aria-selected', 'true');
    await userEvent.keyboard('{Home}');
    expect(tab(/^Summary/)).toHaveFocus();
    expect(tab(/^Summary/)).toHaveAttribute('aria-selected', 'true');
  });

  it('leaves other keys to the page', async () => {
    at('');
    act(() => tab(/^Summary/).focus());
    const added = entriesAdded();
    await userEvent.keyboard('{ArrowDown}{a}');
    expect(added()).toBe(0);
    expect(tab(/^Summary/)).toHaveFocus();
  });

  it('is reached by Tab once, on the selected tab; the panel is the next stop', async () => {
    at('?tab=drivers');
    render(<button type="button">After</button>);
    await userEvent.tab();
    expect(tab(/^Drivers/)).toHaveFocus();
    await userEvent.tab();
    expect(screen.getByRole('tabpanel')).toHaveFocus();
    await userEvent.tab();
    expect(screen.getByRole('button', { name: 'After' })).toHaveFocus();
  });

  it.each([
    ['Alt', 'ArrowLeft', { altKey: true }],
    ['Cmd', 'ArrowRight', { metaKey: true }],
    ['Ctrl', 'End', { ctrlKey: true }],
  ])('leaves %s with a key to the browser (Back, Forward)', (_name, key, modifier) => {
    at('?tab=risk');
    const added = entriesAdded();
    const event = new KeyboardEvent('keydown', {
      key,
      bubbles: true,
      cancelable: true,
      ...modifier,
    });
    act(() => {
      tab(/^Risk distribution/).dispatchEvent(event);
    });
    expect(event.defaultPrevented).toBe(false);
    expect(added()).toBe(0);
    expect(tab(/^Risk distribution/)).toHaveAttribute('aria-selected', 'true');
  });

  it('scrolls its strip to the chosen tab, and never the window', async () => {
    at('');
    const intoView = vi.spyOn(Element.prototype, 'scrollIntoView');
    const list = screen.getByRole('tablist');
    Object.defineProperty(list, 'clientWidth', { configurable: true, value: 300 });
    screen.getAllByRole('tab').forEach((t, i) => {
      Object.defineProperty(t, 'offsetLeft', { configurable: true, value: i * 200 });
      Object.defineProperty(t, 'offsetWidth', { configurable: true, value: 190 });
    });
    await userEvent.click(tab(/^Retention queue/));
    expect(list.scrollLeft).toBe(600 + 190 - 300);
    await userEvent.click(tab(/^Summary/));
    expect(list.scrollLeft).toBe(0);
    expect(intoView).not.toHaveBeenCalled();
  });
});

describe('scrolledTo', () => {
  const strip = (scrollLeft: number) =>
    ({ scrollLeft, clientWidth: 300 }) as unknown as HTMLElement;
  const at = (offsetLeft: number) => ({ offsetLeft, offsetWidth: 100 }) as unknown as HTMLElement;

  it('leaves a strip that already shows the tab where it is', () => {
    expect(scrolledTo(strip(50), at(100))).toBe(50);
  });

  it('scrolls back to a tab cut on the left, and on to one cut on the right', () => {
    expect(scrolledTo(strip(150), at(100))).toBe(100);
    expect(scrolledTo(strip(0), at(250))).toBe(50);
  });
});
