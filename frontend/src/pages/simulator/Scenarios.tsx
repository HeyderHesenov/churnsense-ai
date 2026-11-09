/**
 * "Compare scenarios": up to four responses kept as they came, beside the one
 * on screen. A pin holds the server's echo of its assumptions and the point
 * at its cut, so each row's figures are exactly a response's; nothing is
 * subtracted from anything. Only figures for the assumptions set can be
 * pinned, and Apply sets a pin's assumptions and cut again, for the API to
 * answer afresh. Pins live as long as the page.
 */
import { useRef, useState } from 'react';
import { flushSync } from 'react-dom';
import type { Sweep } from '../../api/queries';
import { Button } from '../../components/ui/Button';
import { Card, CardHeader, Tag } from '../../components/ui/Surface';
import { cut, int, signedMoney } from '../../lib/format';
import { pinSummary, recallText } from './copy';
import { samePin, type Pin, type Selection } from './simulator';
import styles from './Simulator.module.css';

/** Four, as the artboard has it: more rows than that stop being a comparison. */
const MAX_PINS = 4;

interface ScenariosProps {
  sweep: Sweep | undefined;
  selected: Selection | null;
  /** The figures shown are the response to the assumptions set. */
  current: boolean;
  onApply: (pin: Pin) => void;
}

export function Scenarios({ sweep, selected, current, onApply }: ScenariosProps) {
  const [pins, setPins] = useState<(Pin & { id: number })[]>([]);
  const next = useRef(1);
  const pinButton = useRef<HTMLButtonElement>(null);
  const removes = useRef(new Map<number, HTMLButtonElement>());
  const point = sweep && selected ? sweep.points[selected.index] : undefined;
  const candidate =
    sweep && selected && point
      ? {
          assumptions: sweep.assumptions,
          t: selected.following ? '' : cut(point.threshold),
          point,
          churners: sweep.churners,
          atRecommendation: selected.index === selected.recommended,
        }
      : null;
  const pinned = candidate !== null && pins.some((pin) => samePin(pin, candidate));
  const full = pins.length >= MAX_PINS;
  const canPin = current && candidate !== null && !pinned && !full;
  const why = pinned
    ? 'These figures are pinned.'
    : full
      ? 'Four scenarios are pinned: remove one to pin another.'
      : !current
        ? 'Only figures for the assumptions set can be pinned.'
        : null;

  const pin = () => {
    if (!canPin) return;
    const id = next.current++;
    setPins((all) => [...all, { ...candidate, id }]);
  };
  const remove = (id: number) => {
    const at = pins.findIndex((p) => p.id === id);
    const rest = pins.filter((p) => p.id !== id);
    flushSync(() => setPins(rest));
    const target = rest[at] ?? rest[at - 1];
    (target ? removes.current.get(target.id) : pinButton.current)?.focus();
  };

  return (
    <Card titleId="scenarios-title" variant="flush" className={styles.scenarios}>
      <div className={styles.scenariosHead}>
        <CardHeader
          id="scenarios-title"
          title="Compare scenarios"
          subtitle="Pin up to four responses and set any of them again. Each row is a response as the API gave it."
          aside={
            <Button
              ref={pinButton}
              variant="sim"
              size="md"
              aria-disabled={!canPin}
              aria-describedby={why ? 'pin-why' : undefined}
              onClick={pin}
            >
              Pin these figures
            </Button>
          }
        />
        {why && (
          <p id="pin-why" className={styles.pinWhy}>
            {why}
          </p>
        )}
      </div>
      {pins.length === 0 ? (
        <p className={styles.emptyPins}>
          Nothing pinned yet. Pin these figures, change an assumption and pin again to compare them
          side by side.
        </p>
      ) : (
        <div className={styles.tableWrap}>
          <table className={styles.pins}>
            <caption className="sr">Pinned scenarios</caption>
            <thead>
              <tr>
                <th scope="col">Assumptions</th>
                <th scope="col">Cut</th>
                <th scope="col">Flagged</th>
                <th scope="col">Recall</th>
                <th scope="col">Net</th>
                <th scope="col">
                  <span className="sr">Actions</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {current && candidate && (
                <tr className={styles.nowRow}>
                  <td>
                    <Tag tone="sim">NOW</Tag> {pinSummary(candidate.assumptions)}
                  </td>
                  <td>{cut(candidate.point.threshold)}</td>
                  <td>{int(candidate.point.flagged)}</td>
                  <td>{recallText(candidate.point, candidate.churners)}</td>
                  <td>
                    {signedMoney(candidate.point.net_benefit, candidate.assumptions.currency)}
                  </td>
                  <td />
                </tr>
              )}
              {pins.map((p) => {
                const name = `${pinSummary(p.assumptions)} at ${cut(p.point.threshold)}`;
                return (
                  <tr key={p.id}>
                    <td>{pinSummary(p.assumptions)}</td>
                    <td>
                      {cut(p.point.threshold)}
                      {p.atRecommendation && <Tag>REC</Tag>}
                    </td>
                    <td>{int(p.point.flagged)}</td>
                    <td>{recallText(p.point, p.churners)}</td>
                    <td>{signedMoney(p.point.net_benefit, p.assumptions.currency)}</td>
                    <td className={styles.pinActions}>
                      <Button
                        size="xs"
                        variant="ghost"
                        aria-label={`Apply ${name}`}
                        onClick={() => onApply(p)}
                      >
                        Apply
                      </Button>
                      <Button
                        ref={(element) => {
                          if (element) removes.current.set(p.id, element);
                          else removes.current.delete(p.id);
                        }}
                        size="xs"
                        variant="quiet"
                        aria-label={`Remove ${name}`}
                        onClick={() => remove(p.id)}
                      >
                        Remove
                      </Button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}
