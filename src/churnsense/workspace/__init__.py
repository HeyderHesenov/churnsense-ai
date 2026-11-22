"""ChurnSense V2: the B2B SaaS workspace domain, kept apart from the V1 telecom demo.

Nothing here imports the telecom contract (``churnsense.data.schema``), the book,
the model or the evaluation code, and nothing in V1 depends on this package
beyond ``churnsense.api.main`` calling ``api.install``, which serves no route
unless the local preview is switched on (``docs/V2_DATA_HUB_PREVIEW.md``).

M1 is the Data Hub import preview only: a CSV of account snapshots is mapped to
the canonical contract (``contract``), validated (``validate``) and summarised.
Nothing is stored - no database, no file, no cache - and nothing is committed.
"""
