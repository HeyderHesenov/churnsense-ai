"""The public demo's customers: generated, never read.

The generator is the only source of the customers a public demo lists. These
tests pin what makes that safe: it is a pure function of a size and a seed, it
opens no file and imports nothing that could, and what it returns passes the
same column contract an upload does - with no outcome and no protected column.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pandas as pd
import pytest

from churnsense.config import Config
from churnsense.data import schema
from churnsense.data.loader import clean_frame
from churnsense.demo import customers as demo

SOURCE = Path(demo.__file__)
FEATURES = schema.model_input_columns(drop_columns=("gender",))


def test_the_same_seed_gives_the_same_customers():
    first, again = demo.demo_customers(), demo.demo_customers()
    pd.testing.assert_frame_equal(first, again)
    assert demo.records_sha256(first) == demo.records_sha256(again)


def test_another_seed_gives_other_customers():
    assert demo.records_sha256(demo.demo_customers(seed=1)) != demo.records_sha256(
        demo.demo_customers(seed=2)
    )


def test_the_book_has_its_stated_size_and_columns():
    frame = demo.demo_customers()
    assert len(frame) == demo.BOOK_SIZE
    assert list(frame.columns) == [schema.ID_COLUMN, *FEATURES]


def test_no_outcome_and_no_protected_column_is_generated():
    columns = set(demo.demo_customers(n=50).columns)
    assert schema.TARGET not in columns
    assert "gender" not in columns


def test_every_customer_is_named_as_a_demo_customer():
    ids = demo.demo_customers()[schema.ID_COLUMN]
    assert ids.is_unique
    assert ids.map(lambda value: re.fullmatch(r"DEMO-[0-9]{6}", value) is not None).all()
    # Never the dataset's own ID format (tests/test_governance.py).
    assert not ids.str.fullmatch(r"[0-9]{4}-[A-Z]{5}").any()


def test_the_customers_pass_the_column_contract(cfg: Config):
    """Cleaned exactly as an upload is: nothing derived, nothing dropped, nothing refused."""
    frame = demo.demo_customers()
    cleaned, report = clean_frame(
        frame,
        required_columns=[schema.ID_COLUMN, *FEATURES],
        deduplicate=False,
        derive_total_charges=False,
    )
    assert len(cleaned) == len(frame)
    assert report.total_charges_blank == 0
    for column, (low, high) in schema.NUMERIC_BOUNDS.items():
        assert cleaned[column].between(low, high).all(), column


@pytest.mark.parametrize("n", [0, -3])
def test_a_book_of_no_customers_is_refused(n):
    with pytest.raises(ValueError):
        demo.demo_customers(n=n)


# --- separation from every dataset ---------------------------------------------------

#: What the generator may import: the column contract, and arithmetic.
ALLOWED_IMPORTS = {"__future__", "hashlib", "numpy", "pandas", "churnsense.data"}
#: Calls that would read something: a file, a dataset, a URL.
READERS = {
    "open",
    "read_csv",
    "read_json",
    "read_parquet",
    "read_text",
    "read_bytes",
    "load",
    "loads",
    "load_clean",
    "read_raw",
    "load_book",
    "urlopen",
}


def _tree() -> ast.Module:
    return ast.parse(SOURCE.read_text(encoding="utf-8"))


def test_the_generator_imports_nothing_that_reads_data():
    imported = set()
    for node in ast.walk(_tree()):
        if isinstance(node, ast.Import):
            imported |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
    assert imported <= ALLOWED_IMPORTS, sorted(imported - ALLOWED_IMPORTS)
    # Of the data layer it takes the column contract only: never the loader.
    names = {
        alias.name
        for node in ast.walk(_tree())
        if isinstance(node, ast.ImportFrom) and node.module == "churnsense.data"
        for alias in node.names
    }
    assert names == {"schema"}


def test_the_generator_reads_no_file_and_no_path():
    called = set()
    for node in ast.walk(_tree()):
        if isinstance(node, ast.Call):
            target = node.func
            called.add(
                target.attr if isinstance(target, ast.Attribute) else getattr(target, "id", "")
            )
    assert not called & READERS, sorted(called & READERS)
    source = SOURCE.read_text(encoding="utf-8")
    assert "Path(" not in source
    assert "raw_data_file" not in source


def test_the_generator_takes_only_a_size_and_a_seed():
    """Nothing can be passed in to sample, copy or perturb: there is no frame parameter."""
    function = next(
        node
        for node in _tree().body
        if isinstance(node, ast.FunctionDef) and node.name == "demo_customers"
    )
    assert [a.arg for a in function.args.args] == ["n", "seed"]
    assert not function.args.kwonlyargs and function.args.vararg is None
