/**
 * The formula block: how the API works the money out, as its documentation
 * states it. Shown in the Advanced controls and on the Method tab.
 */
import styles from './Simulator.module.css';

/** How the API works the money out, as its documentation states it. */
export function Formula() {
  return (
    <div className={styles.formula}>
      <p className={styles.formulaTitle}>How the API works the money out</p>
      <p>
        <span>value</span> = monthly charges × horizon × margin
      </p>
      <p>
        <span>retained</span> = Σ flagged churners × value × success
      </p>
      <p>
        <span>cost</span> = flagged × offer
      </p>
      <p className={styles.formulaNet}>
        <span>net</span> = retained − cost
      </p>
    </div>
  );
}
