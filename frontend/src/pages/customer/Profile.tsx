/**
 * The customer's 18 inputs: as a profile, the strongest four attributions
 * highlighted; or, for a new customer, as a form. The form keeps to the data
 * contract's rules as it is edited (``coherent``) and says which amount it
 * cannot send, and why.
 */
import type { CustomerFeatures, Explanation } from '../../api/queries';
import { Button } from '../../components/ui/Button';
import { Select } from '../../components/ui/Field';
import type { Option } from '../../components/ui/Toggle';
import { COMPACT } from '../../lib/labels';
import { cx } from '../../lib/style';
import { amountMessage, inputsTag, LIVE_NOTE, PROFILE_NOTE } from './copy';
import {
  ADD_ONS,
  amountProblems,
  coherent,
  estimatedTotal,
  featureValue,
  GROUPS,
  noTenure,
  strongest,
  type Amount,
  type Bounds,
  type Draft,
} from './customer';
import styles from './Customer.module.css';

/** The form's first box, which "Score a new customer" moves the focus to. */
export const TENURE_ID = 'customer-tenure';

export function Profile({
  features,
  strongest: explanation,
}: {
  features: CustomerFeatures | null;
  /** The explanation whose strongest four are highlighted, once it is the current one. */
  strongest: Explanation | undefined;
}) {
  const strong = strongest(explanation);
  return (
    <section aria-labelledby="profile-title" className={styles.profile}>
      <div className={styles.profileHead}>
        <h2 id="profile-title" className={styles.cardTitle}>
          Profile
        </h2>
        {features && <span className={styles.tag}>{inputsTag(Object.keys(features).length)}</span>}
      </div>
      {features &&
        GROUPS.map((group) => (
          <div key={group.title} className={styles.group}>
            <span className={styles.groupTitle}>{group.title}</span>
            <dl className={styles.facts}>
              {group.features.map((feature) => (
                <div key={feature} className={styles.fact}>
                  <dt>{COMPACT[feature]}</dt>
                  <dd data-strong={strong.has(feature)}>
                    <span>{featureValue(feature, features)}</span>
                    {strong.has(feature) && <span className="sr"> (among the four strongest)</span>}
                  </dd>
                </div>
              ))}
            </dl>
          </div>
        ))}
      <p className={styles.note}>{PROFILE_NOTE}</p>
    </section>
  );
}

const CONTRACTS: Option<Draft['Contract']>[] = [
  { value: 'Month-to-month', label: 'Month-to-month' },
  { value: 'One year', label: 'One year' },
  { value: 'Two year', label: 'Two year' },
];
const PAYMENTS: Option<Draft['PaymentMethod']>[] = [
  { value: 'Electronic check', label: 'Electronic check' },
  { value: 'Mailed check', label: 'Mailed check' },
  { value: 'Bank transfer (automatic)', label: 'Bank transfer (automatic)' },
  { value: 'Credit card (automatic)', label: 'Credit card (automatic)' },
];
const INTERNET: Option<Draft['InternetService']>[] = [
  { value: 'Fiber optic', label: 'Fiber optic' },
  { value: 'DSL', label: 'DSL' },
  { value: 'No', label: 'None' },
];
/** Multiple lines with a phone service, and the one value without (set by the checkbox). */
const LINES: Option<Draft['MultipleLines']>[] = [
  { value: 'No', label: 'No' },
  { value: 'Yes', label: 'Yes' },
];
const NO_PHONE: Option<Draft['MultipleLines']>[] = [
  { value: 'No phone service', label: 'No phone service' },
];

type YesNo = 'PhoneService' | 'PaperlessBilling' | 'Partner' | 'Dependents';
type Toggleable = YesNo | (typeof ADD_ONS)[number] | 'SeniorCitizen';

/** The checkboxes, in the artboard's order, with its words. */
const SERVICES: [Toggleable, string][] = [
  ['PhoneService', 'Phone service'],
  ['OnlineSecurity', 'Online security'],
  ['OnlineBackup', 'Online backup'],
  ['DeviceProtection', 'Device protection'],
  ['TechSupport', 'Tech support'],
  ['StreamingTV', 'Streaming TV'],
  ['StreamingMovies', 'Streaming movies'],
  ['PaperlessBilling', 'Paperless billing'],
];
const HOUSEHOLD: [Toggleable, string][] = [
  ['SeniorCitizen', 'Senior'],
  ['Partner', 'Partner'],
  ['Dependents', 'Dependents'],
];

interface LiveFormProps {
  form: Draft;
  /** The API's bounds on the amounts, once ``/model-info`` is in. */
  bounds: Bounds | undefined;
  onChange: (form: Draft) => void;
  onReset: () => void;
}

export function LiveForm({ form, bounds, onChange, onReset }: LiveFormProps) {
  const change = (changes: Partial<Draft>) => onChange(coherent({ ...form, ...changes }));
  const problems = amountProblems(form, bounds);
  const fieldProblem = (field: Amount) => problems.find((p) => p.field === field);
  const noInternet = form.InternetService === 'No';
  const noPhone = form.PhoneService === 'No';
  // With no tenure nothing is billed: total charges are fixed, and say why.
  const unbilled = noTenure(form);

  const amount = (field: Amount, label: string, inputMode: 'numeric' | 'decimal') => {
    const problem = fieldProblem(field);
    const id = field === 'tenure' ? TENURE_ID : `customer-${field}`;
    const fixed = field === 'TotalCharges' && unbilled;
    const described = [problem && `${id}-problem`, fixed && `${id}-fixed`].filter(Boolean);
    return (
      <span className={styles.field}>
        <label htmlFor={id} className={styles.fieldLabel}>
          {label}
        </label>
        <input
          id={id}
          type="text"
          inputMode={inputMode}
          autoComplete="off"
          spellCheck={false}
          className={styles.input}
          value={form[field]}
          readOnly={fixed}
          aria-invalid={problem !== undefined}
          aria-describedby={described.length > 0 ? described.join(' ') : undefined}
          onChange={(event) => change({ [field]: event.target.value })}
        />
        {fixed && (
          <span id={`${id}-fixed`} className={styles.fieldNote}>
            Nothing is billed before the first month.
          </span>
        )}
      </span>
    );
  };

  const check = ([key, label]: [Toggleable, string]) => {
    const on = key === 'SeniorCitizen' ? form.SeniorCitizen === '1' : form[key] === 'Yes';
    const disabled = noInternet && (ADD_ONS as readonly string[]).includes(key);
    return (
      <label key={key} className={styles.check} data-disabled={disabled}>
        {label}
        <input
          type="checkbox"
          checked={on}
          disabled={disabled}
          onChange={(event) =>
            change({
              [key]:
                key === 'SeniorCitizen'
                  ? event.target.checked
                    ? '1'
                    : '0'
                  : event.target.checked
                    ? 'Yes'
                    : 'No',
            })
          }
        />
      </label>
    );
  };

  const total = estimatedTotal(form);
  return (
    <section aria-labelledby="profile-title" className={styles.profile}>
      <div className={styles.profileHead}>
        <h2 id="profile-title" className={styles.cardTitle}>
          New customer
        </h2>
        <span className={cx(styles.tag, styles.liveTag)}>scored live</span>
      </div>
      <span className={styles.groupTitle}>Account</span>
      <div className={styles.grid2}>
        {amount('tenure', 'Tenure (months)', 'numeric')}
        {amount('MonthlyCharges', 'Monthly charges ($)', 'decimal')}
        <Select
          label="Contract"
          layout="stacked"
          value={form.Contract}
          options={CONTRACTS}
          onChange={(Contract) => change({ Contract })}
        />
        <span className={styles.totalRow}>
          {amount('TotalCharges', 'Total charges ($)', 'decimal')}
          <Button
            size="sm"
            className={styles.times}
            aria-label="Set total charges to tenure times monthly charges"
            title="Set to tenure × monthly charges"
            disabled={total === null || unbilled}
            onClick={() => total !== null && change({ TotalCharges: total })}
          >
            =×
          </Button>
        </span>
      </div>
      <Select
        label="Payment method"
        layout="stacked"
        value={form.PaymentMethod}
        options={PAYMENTS}
        onChange={(PaymentMethod) => change({ PaymentMethod })}
      />
      {/* There before anything is wrong, so a screen reader reads the first message too. */}
      <div aria-live="polite">
        {problems.length > 0 && (
          <ul className={styles.problems}>
            {problems.map((problem) => (
              <li
                key={`${problem.field}-${problem.reason}`}
                id={`${problem.field === 'tenure' ? TENURE_ID : `customer-${problem.field}`}-problem`}
              >
                {amountMessage(problem)}
              </li>
            ))}
          </ul>
        )}
      </div>
      <span className={styles.groupTitle}>Services</span>
      <div className={styles.grid2}>
        <Select
          label="Internet service"
          layout="stacked"
          value={form.InternetService}
          options={INTERNET}
          onChange={(InternetService) => change({ InternetService })}
        />
        <Select
          label="Multiple lines"
          layout="stacked"
          value={form.MultipleLines}
          options={noPhone ? NO_PHONE : LINES}
          disabled={noPhone}
          onChange={(MultipleLines) => change({ MultipleLines })}
        />
      </div>
      <div className={styles.checks}>{SERVICES.map(check)}</div>
      <span className={styles.groupTitle}>Household</span>
      <div className={cx(styles.checks, styles.checks3)}>{HOUSEHOLD.map(check)}</div>
      <div className={styles.formActions}>
        <Button variant="ghost" size="sm" onClick={onReset}>
          Reset to the README example
        </Button>
        <span className={styles.note}>{LIVE_NOTE}</span>
      </div>
    </section>
  );
}
