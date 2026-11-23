# V2 Retention Action Center foundation (M3)

The third piece of ChurnSense V2. The V2 workflow is
**Data → Understand risk → Prioritize → Act → Measure**:

| Step | Milestone | State |
|---|---|---|
| Data | M1 Data Hub preview (`docs/V2_DATA_HUB_PREVIEW.md`) | Served locally, stateless |
| Understand risk, Prioritize | M2 Customer Intelligence and Retention Workbench (`docs/V2_RETENTION_WORKBENCH.md`) | Served locally, synthetic, stateless |
| Act | **M3 Retention Action Center: this document** | **Contracts and checks only. Not served** |
| Measure | M4 Retention Impact and Experiments (`docs/V2_RETENTION_IMPACT_EXPERIMENTS.md`) | Contracts and pure analysis only |

M3 is the foundation of the Act step. It defines how a future authenticated
ChurnSense workspace will find a playbook for an M2 risk indicator, propose a
retention task, assign it, require a person's approval before work starts,
move it through its lifecycle, and keep the evidence for why it was proposed.

**Nothing in M3 is operational.** There is no route, no database, no stored
task and no task execution. There are no notifications, no integrations and no
user interface. M3 contains three things:

- a fixed, versioned playbook catalog;
- typed, validated request and response contracts;
- pure functions that check playbook applicability and lifecycle moves.

The code is in `src/churnsense/workspace/`, apart from V1 and from M1 and M2:

| Module | What it does |
|---|---|
| `action_playbooks.py` | The playbook catalog, its indexes and applicability (`applicable`, `require_applicable`) |
| `action_lifecycle.py` | Task statuses, the transition table and the checks for transitions, assignment and due dates |
| `action_schemas.py` | The future API's typed requests and responses, and `check_proposal` |

Tests are in `tests/test_workspace_action_center.py`.

## Playbooks

The catalog is `action_playbooks.CATALOG`, version `action_playbooks.v1`. It is
built into the code. Nothing in it comes from a model, a language model, an
external service or generated text. Each playbook is a frozen record, its
ordered steps are a tuple, and the indexes `BY_ID` and `BY_TRIGGER` are
read-only mappings.

### Mapping to M2 indicators

The triggers are the exact indicator codes of the M2 rule engine
(`risk.RULE_ORDER`, rules version `workbench_rules.v1`). The M2 engine is the
source of truth. Each code triggers exactly one playbook, and no playbook has
any other trigger. The module refuses to import if that stops being true.

| M2 indicator code | Playbook (`playbook_id`, version 1) |
|---|---|
| `payment_past_due` | Payment resolution (`payment_resolution`) |
| `renewal_due` | Renewal readiness (`renewal_readiness`) |
| `inactivity` | Customer re-engagement (`customer_reengagement`) |
| `sessions_decline` | Product usage investigation (`product_usage_investigation`) |
| `key_actions_decline` | Product usage investigation (`product_usage_investigation`) |
| `low_active_user_coverage` | Adoption coverage improvement (`adoption_coverage_improvement`) |

The two usage-decline indicators are closely related, so they share one
playbook. An account with both gets one match that holds both indicators.

### Playbook fields

Every playbook has:

- a stable `playbook_id`;
- an explicit `version`;
- a `title` and a `description`;
- its trigger codes;
- ordered steps, each with a title and a detail;
- `execution: "manual_only"`;
- `requires_human_approval: true`.

A playbook that is not manual, does not require approval, has no steps, or has
no triggers or repeated triggers cannot be constructed.

Steps, in order:

- **Payment resolution:**
  1. Review the billing status through the company's authorized billing process.
  2. Investigate the reported payment issue.
  3. Recommend a human follow-up.
  4. Record the findings and next steps.
- **Renewal readiness:**
  1. Review the renewal date and account context.
  2. Review usage and account history.
  3. Prepare a human-led renewal discussion.
  4. Record risks, questions and agreed next steps.
- **Customer re-engagement:**
  1. Review the activity history.
  2. Investigate possible reasons for reduced activity.
  3. Recommend a human-led check-in.
  4. Record findings and follow-up actions.
- **Product usage investigation:**
  1. Review the usage change in its historical context.
  2. Investigate workflow or adoption barriers.
  3. Recommend product guidance or enablement.
  4. Record findings and follow-up actions.
- **Adoption coverage improvement:**
  1. Review the active-user coverage signal.
  2. Identify adoption gaps.
  3. Recommend onboarding or enablement.
  4. Record findings and follow-up actions.

No step makes an offer or a discount. No step contacts a customer
automatically.

### Applicability

`applicable(assessment)` reads the indicators that an M2 assessment already
holds, and nothing else. It does not re-read a threshold, recalculate a level
or infer a new indicator. When an assessment has several playbooks, they are
listed in the assessment's own order: attention before watch, then by rule
order. The playbook for the lead signal comes first.

These get no playbook:

- an account that is **not assessed** (trialing, paused or canceled);
- an assessed account with **no signals**;
- an indicator code the catalog does not know.

An assessment made by rules other than `workbench_rules.v1` is refused. The
catalog's triggers are written for that rules version.

`require_applicable(assessment, playbook_id)` returns the match, or raises
`ActionRefused` with one of these codes:

- `unknown_playbook`;
- `account_not_assessed`;
- `playbook_not_applicable`.

**A playbook is guidance for a human-led review.** It is not evidence that any
action will keep an account or recover revenue. It does not predict the effect
of an intervention.

## The retention task contract

These are the shapes a future service will return. They are in
`action_schemas.py`. Every model is frozen and refuses unknown fields. Every
list is bounded, and every timestamp must carry a time zone. A refusal never
quotes the value it refused.

### `RetentionTask`

| Field | Meaning |
|---|---|
| `task_id` | UUID, issued by the server |
| `account_id` | The M2 account identifier (`account_external_id`): 1–128 characters, trimmed, no control characters |
| `playbook` | `playbook_id`, `version` and `title` of the playbook version the task follows |
| `evidence` | Why the task exists (see below) |
| `status` | One of the six lifecycle statuses |
| `assignee_id` | A member UUID, or null |
| `due_date` | A calendar date, or null |
| `created` | `{by, at}`: the creator and the time, set by the server |
| `updated_at` | The time of the latest change |
| `approved` | `{by, at}` once a person approved the task |
| `started` | `{by, at}` once work started |
| `completed` | `{by, at, note}` for a completed task |
| `closed` | `{by, at, reason}` for a dismissed or cancelled task |

The contract checks that a task agrees with its status:

- Every record its path required is present, and no other.
- An approved, in-progress or completed task has `approved`.
- An in-progress or completed task has `started`.
- A dismissed task was never approved.
- A cancelled task may carry the approval and start it reached, but never a
  start without an approval.
- `created`, `approved`, `started`, and `completed` or `closed` are in time
  order. `updated_at` is no earlier than the last of them.

### Evidence traceability

`TaskEvidence` records why the task was proposed. It holds:

- `assessment_as_of_date`, the snapshot the assessment read;
- `method` (`rules`) and `method_version`;
- the account's M2 `account_level` on that date;
- `trigger_codes`;
- the triggering `indicators`.

The indicators are in M2's own response shape (`workbench_schemas.Indicator`).
Each one keeps its explanation, its rule text, its observed value, and the
canonical fields and snapshot dates it read. A later reader can see which
account the task concerns and which assessment snapshot it relates to. They can
also see the exact values that made the playbook apply, after the account's
data has moved on.

The contract checks that the evidence is consistent:

- The codes match the indicators one to one, in order.
- Every indicator is from the assessed snapshot.
- An attention indicator implies the attention level.
- A task's evidence names only indicators its playbook version covers.

### Audit events

`TaskAuditEvent` is one recorded change to a task. Its fields are:

- `event_id`;
- `task_id`;
- `sequence`, starting at 1;
- `action`;
- `actor_id`;
- `occurred_at`;
- `from_status`, `to_status`, `assignee_id` and `reason`, as the action needs.

`action` is one of:

- `task_proposed`: always event 1, from null to `proposed`. It may name an
  initial assignee.
- `task_assigned` / `task_unassigned`: names the assignee or none. No status
  change.
- `status_changed`: both statuses. The move must pass the lifecycle check,
  including its reason rule.

The server writes the events. No request creates one.

## Lifecycle and approval

`action_lifecycle.py` defines the six statuses and the transition table
(`TRANSITIONS`):

| Current status | Allowed next statuses |
|---|---|
| `proposed` | `approved`, `dismissed`, `cancelled` |
| `approved` | `in_progress`, `cancelled` |
| `in_progress` | `completed`, `cancelled` |
| `completed` | none (terminal) |
| `dismissed` | none (terminal) |
| `cancelled` | none (terminal) |

`validate_transition(current, target, reason)` is pure and deterministic. It
returns when the move is allowed. Otherwise it raises `ActionRefused` with one
of these codes:

| Code | When |
|---|---|
| `unknown_status` | Either status is not one of the six |
| `no_op_transition` | The task already has the target status |
| `terminal_status` | The task is completed, dismissed or cancelled, so it is never reopened |
| `approval_required` | A proposed task is moved to `in_progress` or `completed`: a person must approve it first |
| `transition_not_allowed` | Any other move outside the table |
| `reason_required` | A move to `dismissed` or `cancelled` without a non-blank reason |

Two other checks are also pure:

- `validate_assignment(status, current, new)` refuses a change on a terminal
  task (`terminal_status`) and a change that changes nothing
  (`no_op_assignment`).
- `validate_due_date(due, today)` refuses a date before `today`
  (`due_date_in_past`) or more than 366 days after it (`due_date_too_far`).
  `today` is passed in, so nothing reads a clock.

`ActionRefused` is a `SchemaValidationError`. The service's existing handler
already answers that as a 422 `invalid_input` in the standard `ErrorResponse`
shape, with the reason in `problems`. The `code` attribute names the rule. No
new error framework is added.

The lifecycle is deliberately small. It has no custom statuses, no
configurable state machine and no workflow engine.

## Future API

These operations are **designed, not implemented**. No route exists. The
contracts are not in the OpenAPI schema, even with the V2 preview switched on.
The paths are the intended design under `/api/v2`, behind authentication.

| Operation | Intended request | Response contract |
|---|---|---|
| List playbooks | `GET /api/v2/playbooks` | `PlaybookCatalog` (from `catalog_out()`) |
| Propose a task | `POST /api/v2/tasks` with `CreateTaskRequest` | `TaskDetail` |
| List tasks | `GET /api/v2/tasks` with `TaskListQuery` | `TaskPage` |
| Task detail | `GET /api/v2/tasks/{task_id}` | `TaskDetail` |
| Task audit trail | `GET /api/v2/tasks/{task_id}/events` | `TaskEventPage` |
| Assign or unassign | `PUT /api/v2/tasks/{task_id}/assignee` with `AssignTaskRequest` | `TaskDetail` |
| Approve, start, complete, dismiss, cancel | `POST /api/v2/tasks/{task_id}/transitions` with `TransitionTaskRequest` | `TaskDetail` |

### Requests

- **`CreateTaskRequest`** takes `account_id`, `playbook_id`, an optional
  `due_date` and an optional `assignee_id`. Nothing else is accepted.
  - The `due_date` must be `YYYY-MM-DD`. A timestamp or a number is refused.
  - A task always starts `proposed`, so the request has no status.
- **`AssignTaskRequest`** requires `assignee_id`. `null` unassigns, so an empty
  body is refused rather than read as an unassignment.
- **`TransitionTaskRequest`** takes `to_status` and an optional `reason`.
  - `to_status` is one of `approved`, `in_progress`, `completed`, `dismissed`
    or `cancelled`. Nothing moves a task back to `proposed`.
  - `reason` is required to dismiss or cancel. Otherwise it is an optional note
    for the audit trail.
  - `reason` is trimmed and at most 500 characters. Line breaks are allowed;
    other control characters are not.
  - The request has no actor field.
- **`TaskListQuery`** filters by `status`, `account_id`, `assignee_id` and
  `playbook_id`.
  - `sort` is `due_date_asc` or `created_desc`.
  - Pages work like the M2 list: `page` from 1, `page_size` from 1 to 100,
    default 25.

### Responses

- **`TaskDetail`** holds the task and the full playbook version it follows.
  It also holds `next_statuses`, the lifecycle's allowed moves from the
  current status. They are computed by the server, not by a browser.
- **`TaskPage`** holds the total, the page, the pages, the sort, the applied
  filters and at most `page_size` rows.
- **`TaskEventPage`** holds one task's audit events in increasing sequence.

### Checking a proposal

`check_proposal(request, assessment, today)` shows the check the future
service must make before it stores anything:

1. The assessment must be the requested account's.
2. The playbook must apply to that assessment.
3. The due date must be in range.

It returns a `TaskProposal` with the playbook reference and the evidence, taken
from the server's assessment. It creates, stores and changes nothing.

## Trust boundaries: tenant and actor

The future workspace will be multi-tenant and authenticated. The contracts are
built so a client cannot claim an identity or evidence:

- **`organization_id` is never accepted from a client.** The service takes the
  organization from the authenticated session. The service must also verify
  that the account, the task and the assignee belong to that organization
  before it reads or changes anything. An assignee must be an authorized
  member of the same organization.
- **The actor is never accepted from a client.** No request has `actor_id`,
  `created_by`, `approved_by` or `completed_by`. The service takes the actor
  from the authenticated principal. It fills in the `created`, `approved`,
  `started`, `completed` and `closed` records and every audit event's
  `actor_id`.
- **Evidence is never accepted from a client.** No request has a risk level, an
  MRR, indicators, trigger codes, a playbook version, a status or timestamps.
  A playbook is checked against the account's current persisted assessment.
  The evidence a task keeps is copied from that assessment by the server.

`SERVER_DERIVED_FIELDS` lists these names. Every request model refuses unknown
fields (`extra="forbid"`), so a field such as `organization_id` is a 422, not
something quietly ignored. The tests send each server-derived field to each
request model and expect a refusal.

The server must derive these identities because a request body is controlled
by whoever sends it. If the body could name the organization, a member of one
organization could reach another organization's accounts. If it could name the
actor, approvals and the audit trail would record whatever the caller claimed.
If it could carry evidence, a task could be justified by numbers M2 never
produced.

## Implemented now, and deferred

**Implemented (M3):**

- the playbook catalog and its exact M2 mapping;
- applicability from recorded M2 evidence;
- typed request and response contracts with their consistency rules;
- the lifecycle table and its transition, assignment and due-date checks;
- the proposal check;
- tests and this document.

**Deliberately deferred.** None of these exist yet:

- persistence: no database, models, migrations, repositories, cache or
  in-memory task state;
- creating, updating, assigning or moving a task;
- any Action Center route;
- authentication, authorization, organizations, memberships, tenant isolation
  or row-level security;
- notifications (email, SMS, Slack), Stripe, CRM or other integrations;
- automatic customer outreach or automatic playbook execution;
- agents, language-model calls or generated actions;
- background jobs, schedulers or queues;
- front-end pages;
- measuring outcomes (the Measure step).

M3 does not change:

- M1 or M2 behaviour, thresholds, synthetic data or validation;
- the feature gate (`CHURNSENSE_V2_DATA_HUB_PREVIEW`);
- `api/main.py` or the middleware;
- the committed OpenAPI artifacts;
- any deployment file;
- the V1 model.

## How a future implementation connects

A later authenticated, multi-tenant phase can build on these pieces without
changing their meaning:

1. An authentication layer establishes the principal: the organization and
   the member.
2. A persistence layer stores tasks and audit events under the organization.
   It also keeps each account's latest M2 assessment.
3. Routes for the operations above validate the request with these models.
   They load the organization's account and assessment. They call
   `check_proposal`, `validate_transition`, `validate_assignment` and
   `validate_due_date`. Then they write the task and its audit event together,
   with the principal as actor, and answer with these response models.
4. The V2 feature gate and the V1 layers (loopback or proxy, hosts, origins,
   body limits, rate limits) stay in front of those routes, as they do for M1
   and M2.
