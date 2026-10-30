/**
 * Save a file built in the browser, and say so when the browser refuses it.
 *
 * A refusal is about what the page showed when the file was asked for
 * (``about``, compared by identity): it goes once anything else is shown, for
 * good. It is said a moment after the status is emptied, so a second refusal
 * in a row is read out again.
 */
import { useEffect, useRef, useState } from 'react';
import { downloadText } from './download';

/** How long an emptied status waits before it speaks. */
const ANNOUNCE_MS = 100;

export function useDownload(about: unknown) {
  const [refused, setRefused] = useState<{ about: unknown } | null>(null);
  if (refused !== null && refused.about !== about) setRefused(null);

  const timer = useRef<number | undefined>(undefined);
  useEffect(() => {
    const pending = timer;
    return () => window.clearTimeout(pending.current);
  }, []);

  const forget = () => {
    window.clearTimeout(timer.current);
    setRefused(null);
  };
  const save = (name: string, text: string) => {
    forget();
    if (!downloadText(name, text)) {
      timer.current = window.setTimeout(() => setRefused({ about }), ANNOUNCE_MS);
    }
  };
  return { unsaved: refused !== null, save, forget };
}
