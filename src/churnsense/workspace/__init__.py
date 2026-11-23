"""ChurnSense V2: the B2B SaaS workspace domain, kept apart from the V1 telecom demo.

Nothing here imports the telecom contract (``churnsense.data.schema``), the book,
the model or the evaluation code, and nothing in V1 depends on this package
beyond ``churnsense.api.main`` calling ``api.install``, which serves no route
unless the local preview is switched on (``docs/V2_DATA_HUB_PREVIEW.md``).

M1 is the Data Hub import preview: a CSV of account snapshots is mapped to the
canonical contract (``contract``), validated (``validate``) and summarised.
Nothing is stored - no database, no file, no cache - and nothing is committed.

M2 is Customer Intelligence and the Retention Workbench (``workbench_api``),
behind the same switch: a deterministic synthetic dataset (``synthetic``),
assessed by transparent rules (``risk``) - not a model, and no probability - and
reported with per-currency exact MRR (``portfolio``). It reads nothing the
preview was sent; the preview keeps nothing (``docs/V2_RETENTION_WORKBENCH.md``).

M3 is the Retention Action Center's foundation, and nothing serves it: a fixed
playbook catalog keyed to M2's indicator codes, the lifecycle of a retention
task with human approval before work starts, and the typed contracts of a
future authenticated API. No route, no storage, no execution
(``docs/V2_RETENTION_ACTION_CENTER.md``).
"""
