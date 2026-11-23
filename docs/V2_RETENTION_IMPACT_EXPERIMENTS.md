# V2 Retention Impact and Experiments foundation (M4)

The fourth piece of ChurnSense V2. The V2 workflow is
**Data → Understand risk → Prioritize → Act → Measure**:

| Step | Milestone | State |
|---|---|---|
| Data | M1 Data Hub preview (`docs/V2_DATA_HUB_PREVIEW.md`) | Served locally, stateless |
| Understand risk, Prioritize | M2 Customer Intelligence and Retention Workbench (`docs/V2_RETENTION_WORKBENCH.md`) | Served locally, synthetic, stateless |
| Act | M3 Retention Action Center (`docs/V2_RETENTION_ACTION_CENTER.md`) | Contracts and checks only |
| Measure | **M4 Retention Impact and Experiments: this document** | **Contracts, pure metrics and a descriptive analysis only. Not served** |

## Why measure

A retention team needs to know what happened to the customers it started with.
How many stayed? How much of their recurring revenue stayed? What was observed
after the team acted? M2 shows which accounts show risk signals today, and M3
defines the tasks a team proposes for them. Neither records what happened next.
M4 adds that record, and defines how it is counted.

M4 keeps three things apart. They are separate in the schemas, the functions
and the wording:

1. **Observed business outcomes.** These are facts recorded from an
   authoritative source, such as a billing or customer-management system. For
   example: an account was retained or churned at the end of its window, with
   its starting and ending MRR.
2. **Descriptive intervention association.** A completed M3 task can be linked
   to a later observed outcome of the same account. The link records order in
   time, nothing more. It does not show that the task changed the outcome.
3. **Experiment comparison.** Observed outcomes can be summarised by the arms
   accounts were assigned to before the experiment started. In M4 that summary
   is descriptive only. It is not a causal estimate and not a significance
   test, and it never names a winning arm.

**Nothing in M4 is operational.** There is no route, no stored outcome or
experiment, no enrollment, no randomization and no execution. M4 uses nothing
from M2's synthetic data. No synthetic account, status or risk signal is ever
treated as an outcome.

The code is in `src/churnsense/workspace/`, in the same flat layout as M1 to M3:

| Module | What it does |
|---|---|
| `outcome_schemas.py` | The outcome record, its taxonomy and checks, metric result shapes, and the task-to-outcome association |
| `retention_metrics.py` | Pure logo retention, GRR, NRR and coverage |
| `experiment_schemas.py` | Experiment definition, assignment, outcome link and analysis result shapes |
| `experiment_analysis.py` | Pure assignment checks, outcome linking and the descriptive analysis |

Tests are in `tests/test_workspace_retention_impact.py` and
`tests/test_workspace_experiments.py`. Every outcome, assignment and experiment
in them is test data, written by hand. None of it is a measured result.

## The outcome record

`AccountOutcome` is one account's outcome for one measurement window. A client
submits only the facts (`OutcomeSubmission`). The server adds the rest.

| Field | Meaning |
|---|---|
| `outcome_id` | UUID, issued by the server |
| `account_id` | The M1/M2 account identifier. It follows M3's rules (1–128 characters, trimmed, no control characters) and, as M1 refuses, may not be an email address or phone number |
| `window_start`, `window_end` | The measurement window. `window_end` is after `window_start` |
| `observed_on` | The day the source was read for the state at `window_end`. It is on or after `window_end` |
| `status` | `retained`, `churned` or `unknown` |
| `currency` | Three uppercase letters, as M1 accepts it. One record has one currency |
| `starting_mrr` | MRR on `window_start`. Zero means the account was not paying then |
| `ending_mrr` | MRR on `window_end`, or null. The field is required even when null |
| `source_reference` | Optional reference in the source system. Bounded, with no contact details |
| `provenance` | `source_system` (`billing_system`, `crm`, `manual_import`) and `recorded` (by whom, when). Set by the server |

### Outcome statuses

| Status | Meaning | Evidence required |
|---|---|---|
| `retained` | The source shows at least one active paid subscription at the end of the window | `ending_mrr` given and above zero |
| `churned` | The source shows no active paid subscription at the end of the window | `ending_mrr` given and exactly zero |
| `unknown` | The source could not establish the account's state | `ending_mrr` null |

An `unknown` outcome is never read as churned or retained, and its missing
ending MRR is never read as zero. A known ending MRR always decides the status.
So an `unknown` record with an amount is refused.

### Other rules

- **Money** follows the Data Hub's form: digits, at most 12 before the point and
  4 after, with no sign, symbol, separator or exponent. It is held as `Decimal`
  and serialized as a JSON string (`"1250.50"`).
  - A JSON number is refused, so no amount passes through a binary float.
  - Negative amounts, `NaN` and infinities are refused.
- **Dates** are `YYYY-MM-DD` between 1900-01-01 and 2199-12-31. Nothing reads
  the machine's date.
- **Provenance.** A record cannot be recorded before the day it was observed.
  Timestamps must carry a time zone.
- **Duplicates.** There is one outcome per account and window: the key is
  `(account_id, window_start, window_end)`. `check_unique_outcomes` lists every
  repeated key and every repeated `outcome_id` by position. An
  `OutcomeImportRequest` of up to 1,000 records refuses a repeated key. The
  metrics and the analysis refuse duplicates before counting.

### What is never an outcome

Churn is never inferred from any of these:

- a trialing, paused or canceled snapshot;
- an M2 level or indicator;
- a synthetic account;
- a completed or dismissed M3 task.

The M4 modules do not import M2's rules, synthetic data or snapshots, and a test
holds them to that.

## Retention metrics

`retention_metrics(outcomes, window, as_of)` measures the records for one
measurement window, as of an explicit date. It is pure and deterministic: input
order does not change the result. Results are structured models, not display
strings.

### Inclusion

Every record given is accounted for in `coverage`:

| Group | Rule | Used in the rates? |
|---|---|---|
| `other_window` | The record's window is not the one measured | No |
| `not_in_starting_cohort` | `starting_mrr` is zero: not a paying customer on the window's first day, for example a customer acquired later | No |
| `immature` | Starting cohort, but the window ends after `as_of` | No |
| `not_yet_observed` | Starting cohort, the window has ended, but the outcome was observed after `as_of` | No |
| `unknown` | Matured, outcome unknown | No. It is counted, and its starting MRR is reported |
| `retained`, `churned` | Matured, outcome observed | Yes |

The counts always add up, and a test checks that:

- `eligible = immature + not_yet_observed + matured`;
- `matured = retained + churned + unknown`.

### Definitions

The definitions are also in `retention_metrics.DEFINITIONS`, which every result
carries.

| Metric | Numerator | Denominator | Notes |
|---|---|---|---|
| Logo retention rate | Retained accounts | Retained + churned accounts (matured starting cohort) | Unknown outcomes are in neither count |
| GRR | Sum of `min(starting_mrr, ending_mrr)` | Sum of `starting_mrr` | Per currency, over matured starting-cohort accounts with an observed outcome. Expansion is excluded, so GRR cannot exceed 100 |
| NRR | Sum of `ending_mrr` | Sum of `starting_mrr` (same accounts as GRR) | Per currency. Expansion and contraction count, so NRR may exceed 100 |
| Outcome coverage | Observed outcomes (retained + churned) | Matured accounts | Shows how much of the matured cohort has a known outcome |
| Revenue coverage | Starting MRR of observed accounts | Starting MRR of matured accounts | Per currency |

- A churned account (ending MRR exactly zero) stays in every denominator.
- New customers never enter the starting cohort: their record is for a later
  window, or their starting MRR is zero.
- A missing or unknown financial outcome is in no sum.

### Currencies

GRR, NRR and revenue coverage are computed separately for each currency, listed
by currency code. No amount is ever added to another currency's. Logo counts
are counts of accounts, so they are reported across currencies, as M2 does.

### Rounding and empty denominators

- Sums are exact: the Data Hub's `MONEY` context raises rather than round.
- A percentage is the exact ratio, cut toward zero to one decimal place, as M2
  shows its percentages. For example, 81.39% is shown as 81.3. The exact
  numerator and denominator are always reported beside it.
- With no denominator, the percentage is `null`, never 0%, and
  `unavailable_reason` says why. For example: the window ends after `as_of`, or
  no matured account has an observed outcome.

### Limitations

- The metrics see only the records they are given. Every account in the cohort
  needs a record to be visible: a future service records `unknown` for an
  account whose outcome it cannot establish, rather than leaving it out.
- The metrics describe what was observed. They do not say why accounts stayed
  or left.

## Linking a task to an outcome

`TaskOutcomeAssociation` links a completed M3 task (`RetentionTask`) to an
observed outcome of the **same** account. `check_association` refuses the link
with an `ActionRefused` code (M3's existing structured refusal) in these cases:

| Code | When |
|---|---|
| `account_mismatch` | The task and the outcome are different accounts'. A task cannot be silently attached to an unrelated account |
| `task_not_completed` | The task is not completed. Only completed tasks are linked |
| `outcome_not_observed` | The outcome is `unknown` |
| `task_after_window` | The task was completed after the window's last day |

The `relationship` is derived from the task's completion date (in UTC) and the
outcome's window:

- `task_completed_before_window`, or
- `task_completed_during_window`.

The association also records:

- `interpretation: "observed_temporal_association"`;
- the task's playbook version;
- the window;
- the observed status.

Its record has no field for impact, revenue saved, churn avoided, attribution
or effect, and a test checks the field names.

**Why association is not attribution.** A task completed before an account
renewed does not show that the task changed the renewal: the account might
have renewed anyway. Completing a task does not mean a customer was kept.
Comparing outcomes with and without an action needs a controlled comparison,
which is what experiments are for.

Not every outcome needs a task, and no link is created without the two records
behind it. The request (`LinkTaskOutcomeRequest`) names only the task and the
outcome. The server reads both, checks them, and derives the account and the
relationship.

## Experiments

### Definition

`ExperimentDefinition` is immutable and versioned. A changed design is a new
version. Its fields are:

- the experiment ID and version, both issued by the server;
- `name`, `objective` and `hypothesis`, which are bounded text with no contact
  details;
- `primary_metric`: only `logo_retention_rate`;
- `randomization_unit`: only `account`;
- exactly two `arms`: `control` first, then `treatment`. Each arm has a whole
  `allocation_percent` from 1 to 99, and the two add up to exactly 100. Each
  arm also has a description of what its accounts receive;
- `enrollment_start` and `enrollment_end`: at most 366 days, the end not
  before the start;
- `outcome_window_days`: from 7 to 366;
- `analysis_principle`: only `intention_to_treat`;
- `created`: who recorded it, and when. It is recorded no later than the
  enrollment start, so the hypothesis and arms are declared before any account
  is assigned.

Multi-arm, cluster, adaptive and bandit designs do not exist in M4.

### Eligibility, before assignment

The primary metric, `logo_retention_rate`, measures whether **existing paying
accounts** are still paying at the end of their window. An account is eligible
for this experiment only if its trusted M2 baseline snapshot
(`snapshot.Snapshot`, the canonical `account_snapshot.v0` record) shows all of
these:

- MRR **above zero**;
- a valid currency (three uppercase letters);
- an `as_of_date` no later than the assignment's UTC date.

M4 does not add a second account or revenue model. The amount is the
snapshot's `mrr`, held as M1's exact `Decimal`.

**Trusted, server-derived evidence.** Each `ExperimentAssignment` carries an
immutable `eligibility` record (`EligibilityEvidence`) with these fields:

- `account_id`;
- `snapshot_date`;
- `baseline_mrr`;
- `currency`.

The future enrollment service builds it from the account's authoritative
snapshot with `eligibility_evidence(snapshot)`. No request can state it:
`EnrollAccountRequest` names only the account, and `eligibility`,
`baseline_mrr` and `snapshot_date` are in `SERVER_DERIVED_FIELDS`. M4 sets no
maximum snapshot age; choosing an appropriate current snapshot is the
enrollment service's job.

An assignment is refused, with a named validation error, when:

- its evidence is missing, or any evidence field is missing;
- the baseline MRR is zero ("the baseline MRR must be above zero") or negative;
- the amount is a float, or not in M1's money form;
- the currency is invalid;
- the snapshot is dated after the assignment ("the baseline snapshot is dated
  after the assignment"), compared as UTC dates;
- the evidence belongs to another account.

**Why zero-MRR accounts cannot enter.** An account paying nothing at the
baseline is not an existing paying customer. If it started paying during the
window, its ending MRR would be positive and it would read as *retained*, which
would inflate logo retention with what is really new revenue. It is therefore
kept out before randomization, not removed afterwards.

**Why before assignment.** Eligibility must be decided before the arm is known.
Dropping accounts after assignment, on information that may differ by arm,
would break the comparability that randomization gives. Once an account is
validly assigned it stays in its arm for good: see the matured ITT population
below.

An assignment that reaches `check_assignments` or `analyze` without valid
evidence is refused as a protocol violation ("assignment N is not eligible:
..."), for example one built without the contract's checks. It is neither
silently excluded nor counted.

### Assignment

`ExperimentAssignment` records one account's arm in one experiment version. Its
fields are:

- the assignment ID;
- the experiment ID and version;
- the account;
- the arm;
- `assigned_at`, a timezone-aware timestamp;
- `assignment_method: "server_randomized"`;
- `eligibility`, the baseline evidence above.

No request chooses an arm or states a baseline. `EnrollAccountRequest` names
only the account; the future server assigns the arm and records the evidence.

M4 does not randomize, enroll or run anything. The record only represents an
assignment.

`check_assignments(definition, assignments)` lists every problem by position:

- another experiment;
- another version;
- an assignment date (UTC) outside the enrollment period;
- an account assigned twice in the version;
- a repeated assignment ID;
- evidence that does not admit the account.

### Outcome windows and links

An account's outcome window starts on its UTC assignment date and ends
`outcome_window_days` later. `link_outcome(definition, assignment, outcome)`
returns an `ExperimentOutcome`, which carries the assigned arm. It refuses:

- an outcome of another account (`account_mismatch`);
- an outcome of another window (`window_mismatch`);
- an outcome in another currency than the assignment's baseline
  (`currency_mismatch`);
- an outcome whose `starting_mrr` is not the assignment's `baseline_mrr`
  (`baseline_mismatch`). The amounts are compared as values, so `100.0` and
  `100.00` are the same;
- an assignment of another version.

The assignment's evidence is authoritative. A conflicting outcome is refused,
never corrected or normalised. An outcome therefore always describes the same
baseline cohort that was randomized.

The account outcome record is reused; there is no second outcome taxonomy.

### Descriptive analysis

`analyze(definition, assignments, outcomes, as_of)` is pure and deterministic.
It refuses:

- invalid or ineligible assignments;
- assignments dated after `as_of`;
- duplicate outcomes;
- an outcome of an assignment's window whose currency or starting MRR differs
  from the assignment's baseline. Every such outcome is listed.

It takes no task status and no exposure, so it cannot reassign an account.

#### The matured ITT population

Eligibility was settled before assignment. From here on, the analysis only
keeps every eligible, randomized account in place.

**Intention to treat:** every assignment is analysed in the arm it was assigned
to. None is moved, dropped or re-labelled because of any of these:

- a missing or unknown outcome;
- non-compliance or an intervention that was never delivered;
- a task's status.

An arm's **matured ITT population** is every account assigned to it whose
outcome window has ended by `as_of`. Accounts whose window has not ended
(`immature`) are not in it yet.

For each arm the analysis reports these counts:

| Count | Meaning |
|---|---|
| `assigned` | Accounts assigned to the arm |
| `immature` | The outcome window ends after `as_of`: not yet in the population |
| `matured` | The matured ITT population: `assigned − immature` |
| `retained`, `churned` | A recorded, observed outcome |
| `unknown` | The outcome was recorded as unknown |
| `missing` | The window has ended, but no outcome was recorded and observed by `as_of` |

The counts always add up: `matured = retained + churned + unknown + missing`.

#### The complete-data ITT retention rate (primary)

`itt_retention_rate` is retained / matured population × 100.

- **It is given only when the data is complete**, meaning every account in the
  matured population has a recorded `retained` or `churned` outcome.
- **If any outcome in the population is missing or unknown**, the rate is null.
  `unavailable` is `incomplete_outcome_data`, and the reason gives the missing
  and unknown counts.
- **If the population is empty**, `unavailable` is `no_matured_assignments`.
- **The population always counts every account**, even when the rate is null.

Missing and unknown outcomes are **never imputed**: they are not treated as
churned, and not treated as retained. Either guess would produce a rate, and
both would be inventions.

The primary comparison, `itt_retention_rate_difference` (`basis:
"intention_to_treat_complete_data"`), is the treatment's ITT rate minus the
control's, in percentage points.

- It comes from the exact rates, cut toward zero to one decimal place.
- It is null, with a reason naming each incomplete arm, unless **both** arms
  have a complete-data ITT rate.

#### The observed-outcome diagnostic

`observed_outcome_diagnostic` is a separate, **complete-case** figure. It
reports:

- `retained` (the numerator);
- `observed` (retained + churned, the denominator);
- `unknown` and `missing`;
- `observed_retention_rate` = retained / observed;
- `outcome_coverage` = observed / matured.

Its comparison, `observed_outcome_difference` (`basis:
"observed_outcomes_only"`), is reported alongside the primary comparison.

This diagnostic leaves out every account without an observed outcome.
**It is not an ITT result and is never the primary rate.** If accounts with
missing or unknown outcomes differ from the rest, as they often do, the
observed rate can differ from what the whole arm would show. With complete data
the two rates are equal. The contract refuses an analysis whose primary slot
holds the observed-outcome comparison.

#### Limitations

No monetary figure is reported. No revenue saved or recovered is derived from
either difference.

Each kind of incompleteness appears in `limitations`, as a code and a message,
with its count per arm:

- `immature_assignments`;
- `missing_outcomes`;
- `unknown_outcomes`;
- `incomplete_outcome_data`, for arms whose ITT rate is unavailable. It notes
  that the observed-outcome diagnostic is not the ITT estimate;
- `itt_comparison_unavailable`;
- `outcomes_not_used`, for records that match no assignment's account and
  window.

There is no minimum sample size: the actual counts and coverage are reported.

### Why the result cannot be read as causal

The analysis result has these fixed fields, which cannot take any other value:

- `analysis_type: "descriptive"`;
- `causal_interpretation: "not_established"`;
- `statistical_inference: "not_performed"`;
- a fixed `statement`, saying that the comparison does not establish a causal
  effect, that no statistical test was performed, and that no arm is selected
  as better.

The first limitation is always `descriptive_only`. The result has no field for
any of these:

- a p-value;
- a confidence interval;
- significance;
- a winner;
- a lift or an effect;
- revenue;
- a probability.

A test checks the field names of every experiment contract.

A higher rate in one arm, ITT or observed, is not evidence that the treatment worked.
Without a statistical analysis it may be chance. Missing or unknown outcomes
that differ between arms can also move the observed rates.

## Future API (contracts only)

These operations are **designed, not implemented**. No route exists, and the
OpenAPI schema is unchanged, even with the V2 preview switched on. A test
checks this. The paths are the intended design under `/api/v2`, behind
authentication.

| Operation | Intended request | Contract |
|---|---|---|
| Submit or import outcomes | `POST /api/v2/outcomes` | `OutcomeImportRequest` (of `OutcomeSubmission`) → `AccountOutcome` records |
| Retention metrics for a window | `GET /api/v2/retention/metrics` | `RetentionMetricsQuery` → `RetentionMetrics` |
| Outcome coverage and quality | `GET /api/v2/retention/coverage` | `RetentionMetricsQuery` → `MeasurementCoverage` |
| Create an experiment | `POST /api/v2/experiments` | `CreateExperimentRequest` → `ExperimentDefinition` |
| List experiments | `GET /api/v2/experiments` | `ExperimentList` |
| Record a trusted assignment | `POST /api/v2/experiments/{experiment_id}/versions/{version}/enrollments` | `EnrollAccountRequest` → `ExperimentAssignment` (arm chosen by the server) |
| Descriptive analysis | `GET /api/v2/experiments/{experiment_id}/versions/{version}/analysis` | `ExperimentAnalysisQuery` → `ExperimentAnalysis` |
| Link a completed task to an outcome | `POST /api/v2/task-outcome-links` | `LinkTaskOutcomeRequest` → `TaskOutcomeAssociation` |

Refusals reuse the existing conventions. A contract violation is a 422 through
FastAPI's validation handler. A rule check raises `SchemaValidationError` or
M3's `ActionRefused`, which the service already answers as a 422
`invalid_input` with `problems`.

## Trust boundaries

M4 follows M3's boundaries. `outcome_schemas.SERVER_DERIVED_FIELDS` extends
M3's list with these fields:

- outcome and association IDs;
- provenance and recording metadata;
- experiment ID and version;
- assignment ID, arm, time and method;
- the eligibility evidence (`eligibility`, `baseline_mrr`, `snapshot_date`).

No M4 request has any of them, and every request refuses unknown fields. A test
sends each one to each request and expects a refusal. A request may *name* an
existing task or outcome by its ID (`REFERENCE_FIELDS`), but it never sets one.

The organization and the actor come from the authenticated session, never from
a request. Outcomes, tasks and assignments must belong to the caller's
organization. This check belongs to the future service, because no
organization or membership exists yet.

M4 adds no email address, phone number or personal name. Account identifiers,
source references and experiment text that contain contact details are refused.

## Implemented now, and deferred

**Implemented in M4:**

- typed, immutable outcome contracts with their checks;
- pure logo retention, GRR, NRR and coverage calculations;
- task-to-outcome association checks;
- experiment definition and assignment contracts and checks, including
  pre-assignment eligibility evidence from the M2 snapshot;
- experiment outcome links;
- the pure descriptive experiment analysis;
- tests and this document.

**Deferred.** None of these exist yet:

- persistent storage of outcomes, experiments, assignments or links;
- real enrollment and randomization;
- experiment execution or delivery of any intervention;
- authenticated user and organization context, memberships and tenant
  isolation;
- billing, CRM or other integrations, and notifications;
- statistical inference: tests, intervals and validated causal-effect
  estimation;
- routes, a public API and front-end pages.

M4 does not yet run real experiments. It does not show that any retention
action improves outcomes.

M4 does not change:

- M1, M2 or M3 behaviour: thresholds, synthetic data, validation, playbooks,
  lifecycle or task contracts;
- V1;
- the feature gate or middleware;
- route registration;
- the committed OpenAPI artifacts;
- any deployment file.
