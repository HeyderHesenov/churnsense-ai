/**
 * A text box whose value lives in the address. What is typed is written there
 * (and so drives any request) once it has been still for a moment, and in any
 * case before any other change is made, so it lands in the history entry it
 * was typed in: when the reader pauses makes no difference. One box settling
 * leaves another being typed in alone. Back or Forward drop it, since that
 * entry is no longer shown; a value changed elsewhere (a chip cleared, a
 * preset applied) replaces the box's text.
 */
import { useEffect, useEffectEvent, useState } from 'react';
import { holdDraft } from './router';

/**
 * ``[text, setDraft]`` for a box showing ``value``. ``onSettle`` runs once
 * per draft that differs from ``value``: after ``delay`` ms of stillness, or
 * first thing in the next change of address, whichever comes first.
 */
export function useSettledDraft(
  value: string,
  onSettle: (draft: string) => void,
  delay = 250,
): [string, (draft: string) => void] {
  // ``null``: nothing typed that is not in the address. A draft is always
  // written or dropped before the address changes, so the box then shows
  // whatever the change put there.
  const [draft, setDraft] = useState<string | null>(null);
  const write = useEffectEvent((text: string) => {
    if (text !== value) onSettle(text);
  });
  useEffect(() => {
    if (draft === null) return;
    // Written, the draft is done: the box shows the address again, whatever
    // a change made in the same moment did to it.
    const release = holdDraft(() => {
      write(draft);
      setDraft(null);
    }, delay);
    const drop = () => {
      release();
      setDraft(null);
    };
    window.addEventListener('popstate', drop);
    return () => {
      release();
      window.removeEventListener('popstate', drop);
    };
  }, [draft, delay]);
  return [draft ?? value, setDraft];
}
