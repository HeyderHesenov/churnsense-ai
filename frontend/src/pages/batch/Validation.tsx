/**
 * What became of the chosen file: the status the API answered with (or "Not
 * sent"), what it means, every problem a refusal listed and the notes of an
 * accepted file, all as plain text. Its title is the page's status line.
 *
 * One action button stays in place from the first file on, whatever it says
 * ("Score file", "Try again", "Choose another file", "Remove file"), so
 * pressing it never drops the reader's focus.
 */
import { Button } from '../../components/ui/Button';
import { canScore, type Stage } from './batch';
import { moreProblems, validation, type Served } from './copy';
import styles from './Batch.module.css';

const TITLE_ID = 'validation-title';

interface Action {
  label: string;
  run: 'score' | 'choose' | 'remove';
}

/** The one thing to do next, or nothing before a file is chosen. */
function nextAction(stage: Stage): Action | null {
  switch (stage.kind) {
    case 'idle':
      return null;
    case 'reading':
    case 'staged':
    case 'pending':
      return { label: 'Score file', run: 'score' };
    case 'rateLimited':
    case 'unavailable':
    case 'failed':
      return { label: 'Try again', run: 'score' };
    case 'accepted':
      return { label: 'Score another file', run: 'choose' };
    case 'denied':
      // Any file would be refused alike: the file can only be taken away.
      return { label: 'Remove file', run: 'remove' };
    default:
      // The same bytes would be refused again: refused, too large, unreadable, several.
      return { label: 'Choose another file', run: 'choose' };
  }
}

interface ValidationProps {
  stage: Stage;
  served: Served;
  onScore: () => void;
  /** Opens the file picker. */
  onChoose: () => void;
  onRemove: () => void;
}

export function Validation({ stage, served, onScore, onChoose, onRemove }: ValidationProps) {
  const panel = validation(stage, served);
  const action = nextAction(stage);
  return (
    <section aria-labelledby={TITLE_ID} className={styles.panel} data-tone={panel.tone}>
      <div className={styles.panelHead}>
        <h2 id={TITLE_ID} className={styles.panelTitle}>
          Validation
        </h2>
        <span className={styles.code} data-tone={panel.tone}>
          {panel.code}
        </span>
      </div>
      <p role="status" className={styles.verdict}>
        {panel.title}
      </p>
      {panel.body && <p className={styles.body}>{panel.body}</p>}
      {stage.kind === 'refused' && (
        <ul aria-label="Problems" className={styles.problems}>
          {stage.problems.map((problem, index) => (
            <li key={index}>{problem}</li>
          ))}
          {stage.omitted > 0 && <li>{moreProblems(stage.omitted)}</li>}
        </ul>
      )}
      {stage.kind === 'accepted' && stage.result.notes.length > 0 && (
        <ul aria-label="Notes" className={styles.notes}>
          {stage.result.notes.map((note) => (
            <li key={note}>{note}</li>
          ))}
        </ul>
      )}
      {action && (
        <div className={styles.actions}>
          {/* Marked, not disabled, until the file can be sent: the focus stays on it. */}
          <Button
            variant="primary"
            aria-disabled={action.run === 'score' && !canScore(stage)}
            onClick={{ score: onScore, choose: onChoose, remove: onRemove }[action.run]}
          >
            {action.label}
          </Button>
          {'name' in stage && action.run !== 'remove' && (
            <Button variant="ghost" onClick={onRemove}>
              Remove file
            </Button>
          )}
        </div>
      )}
    </section>
  );
}
