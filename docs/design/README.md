# Dashboard design: the source of truth for `frontend/`

These are the eight artboards of the dashboard design, as they were published on the
Claude Design canvas (`canvas.json` holds the layout, order and titles). The front end in
`frontend/` was first built from them, page for page. It has since been redesigned to a
later version of the design ("design v4": a grouped sidebar, pages split into tabs, new
layouts), which supersedes these artboards where the two differ; that version is kept
as a written specification in [`docs/frontend/PLAN.md`](../frontend/PLAN.md) ("Design v4"),
not as files here. These artboards stay as the record of the first build.
`MASTER_PROMPT.md` is the brief the first implementation followed, and
[`docs/frontend/PLAN.md`](../frontend/PLAN.md) records how the brief was applied to this
repository.

| Artboard | Page | Route |
|---|---|---|
| `Main.dc.html` | Executive overview | `/` |
| `Explorer.dc.html` | Customer explorer | `/explorer` |
| `Patterns.dc.html` | Churn patterns | `/patterns` |
| `Performance.dc.html` | Model performance | `/performance` |
| `Explain.dc.html` | Explainability | `/explainability` |
| `Customer.dc.html` | Single customer | `/customer` |
| `Simulator.dc.html` | Retention simulator | `/simulator` |
| `Batch.dc.html` | Batch scoring | `/batch` |

## What this copy leaves out, and why

The design bundle the artboards came from also held a runnable preview. These parts of it
are deliberately **not** in the repository:

- **`preview/data/*.json`** is a JSON export of every customer in the dataset, with the
  served probability and the train and validation outcomes. This repository does not
  redistribute the IBM Telco data, so the file is not copied.
- **`model.json` and `scorer.js`** are an in-browser copy of the trained model, which
  the prototype used to be interactive. Production scoring goes through the API only, so
  the dashboard, the API and batch scoring cannot disagree.
- **`tools/`** holds the generator that expanded the shared sidebar and header into each
  artboard, a logic checker, and the data export script. The expanded artboards here are
  what those tools produced.

## The one change made to the artboards

Two artboards embedded individual customers from the dataset:

- `Main.dc.html` has 24 retention-queue rows (`QUEUE`);
- `Customer.dc.html` has four picker customers (`CUSTOMERS`).

Their identifiers and attributes were replaced with synthetic `DEMO-` rows from
`tests/fixtures/demo_customers.csv`. The model figures beside them were left in place, so
a placeholder row's probability does not belong to its attributes.

The real customer ID used as placeholder text in `Explorer.dc.html` became `e.g. 1234-ABCDE`.

Nothing else changed: markup, styles, copy and every aggregate figure are exactly as
designed. The artboards load fonts from Google Fonts and read data files that are not
here, so they document the design rather than run.
