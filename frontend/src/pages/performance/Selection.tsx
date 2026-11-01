/**
 * Model selection by the one-standard-error rule: each candidate the rule
 * kept, its cross-validated PR-AUC on training with one standard error
 * either side, and the cutoff (the leader's score less its standard error).
 * The axis is taken from the whiskers and the cutoff; the leader is the one
 * the server names, the chosen one the one it marks. Every candidate,
 * baseline included, is in the table.
 */
import type { Performance } from '../../api/queries';
import { ChartFrame } from '../../components/charts/ChartFrame';
import { Card, CardHeader, Tag } from '../../components/ui/Surface';
import { modelName } from '../../lib/labels';
import { cssVars, cx } from '../../lib/style';
import { CANDIDATES_NOTE, cutoffCaption, cv, selectionNote, selectionSubtitle } from './copy';
import { charted, selectionScale } from './performance';
import styles from './Performance.module.css';

export function Selection({ figures }: { figures: Performance }) {
  const { selection, candidates } = figures;
  const title = 'Model selection: the one-standard-error rule';
  if (!selection || !candidates) {
    return (
      <Card titleId="selection-title" className={styles.card}>
        <CardHeader id="selection-title" title={title} aside={<Tag>TRAIN · CV</Tag>} />
        <p className={styles.unavailable}>
          The model comparison is not recorded in this artifact’s reports.
        </p>
      </Card>
    );
  }

  const shown = charted(candidates);
  const scale = selectionScale(shown, selection.cutoff);
  const ticks = scale.ticks(4);
  const leader = candidates.find((c) => c.model === selection.leader_name);
  const chosen = candidates.find((c) => c.selected);
  const marks = (model: string) =>
    [model === leader?.model && 'leader', model === chosen?.model && 'selected']
      .filter(Boolean)
      .join(', ');

  return (
    <Card titleId="selection-title" className={styles.card}>
      <CardHeader
        id="selection-title"
        title={title}
        subtitle={selectionSubtitle(selection.folds)}
        aside={<Tag>TRAIN · CV</Tag>}
      />
      <ChartFrame
        label={
          `Cross-validated PR-AUC with one standard error for ${shown.length} candidates; ` +
          `cutoff ${cv(selection.cutoff)}` +
          (chosen ? `; ${modelName(chosen.model)} selected.` : '.')
        }
        table={{
          caption: 'Cross-validated PR-AUC of the candidates inside one standard error',
          columns: ['Candidate', 'CV PR-AUC', 'Standard error', 'Marked'],
          rows: shown.map((c) => [
            modelName(c.model),
            cv(c.cv_pr_auc),
            cv(c.cv_se),
            marks(c.model),
          ]),
        }}
      >
        <div className={styles.whiskers}>
          {shown.map((c) => (
            <div key={c.model} className={styles.whiskerRow}>
              <span className={cx(styles.whiskerName, c.selected && styles.chosenName)}>
                {modelName(c.model)} <span className={styles.mono}>{cv(c.cv_pr_auc)}</span>
              </span>
              <span className={styles.whiskerPlot}>
                <span
                  className={cx(styles.whisker, c.selected && styles.chosenWhisker)}
                  style={cssVars({
                    '--lo': scale(c.cv_pr_auc - c.cv_se),
                    '--hi': scale(c.cv_pr_auc + c.cv_se),
                  })}
                />
                <span
                  className={cx(styles.dot, c.selected && styles.chosenDot)}
                  style={cssVars({ '--at': scale(c.cv_pr_auc) })}
                />
                {marks(c.model) && (
                  <span
                    className={styles.whiskerMark}
                    style={cssVars({ '--at': scale(c.cv_pr_auc) })}
                  >
                    {marks(c.model)}
                  </span>
                )}
              </span>
            </div>
          ))}
          <div className={styles.whiskerRow}>
            <span />
            <span className={styles.whiskerAxis}>
              {ticks.map((t) => (
                <span key={t} className={styles.whiskerTick} style={cssVars({ '--at': scale(t) })}>
                  {t.toFixed(3)}
                </span>
              ))}
            </span>
          </div>
          <span
            className={styles.cutoffLine}
            style={cssVars({ '--at': scale(selection.cutoff) })}
          />
        </div>
      </ChartFrame>
      <p className={styles.cutoffCaption}>{cutoffCaption(selection, leader)}</p>
      <table className={styles.candidateTable}>
        <caption className={styles.tableCaption}>Candidate scores, uncalibrated</caption>
        <thead>
          <tr>
            <th scope="col">Candidate</th>
            <th scope="col">CV std</th>
            <th scope="col">Val PR-AUC</th>
            <th scope="col">Val ROC-AUC</th>
          </tr>
        </thead>
        <tbody>
          {candidates.map((c) => (
            <tr key={c.model} className={c.selected ? styles.chosenRow : undefined}>
              <th scope="row">{modelName(c.model)}</th>
              <td>{cv(c.cv_std)}</td>
              <td>{cv(c.val_pr_auc)}</td>
              <td>{cv(c.val_roc_auc)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className={styles.note}>
        {selectionNote(leader, chosen, modelName)} {CANDIDATES_NOTE}
      </p>
    </Card>
  );
}
