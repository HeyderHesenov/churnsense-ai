"""Reading an uploaded CSV into numbered records, in memory, once.

The rules are V1's for an upload (``churnsense.models.predict``), reimplemented
here so the two domains share no code path: comma-separated UTF-8, a byte-order
mark dropped wherever a line starts with one, a semicolon-separated file named
as such, the column and row limits checked while reading, and one tokenizer -
the csv module, in strict mode - deciding what every record is.

Rows are numbered as a spreadsheet shows them: the first record of the file is
row 1 (the header, in a file that starts with it), and a blank line is a row
too, so a reported row number is the one the user sees when they open the file.
A line break inside a quoted value does not start a new row.

A row that is blank, or whose every field is blank, is skipped and reported.
Every message here is fixed text: none repeats a value from the file, and none
carries the csv module's own text.
"""

from __future__ import annotations

import csv
import io
import logging
from collections.abc import Iterator
from dataclasses import dataclass, field

from churnsense.exceptions import SchemaValidationError

logger = logging.getLogger(__name__)

UNREADABLE = "file could not be read as CSV: check its quoting near row {row:,}"


@dataclass(frozen=True, slots=True)
class Record:
    """One non-blank data row: its row number and its fields."""

    row: int
    fields: list[str]


@dataclass(slots=True)
class Header:
    names: list[str]
    row: int


#: Blank rows whose numbers are kept; every one is counted.
BLANK_ROWS_KEPT = 200


@dataclass(slots=True)
class Tally:
    """What reading the rows found besides the records themselves."""

    #: The first ``BLANK_ROWS_KEPT`` blank rows' numbers.
    blank_rows: list[int] = field(default_factory=list)
    blank_count: int = 0
    data_rows: int = 0

    def blank(self, row: int) -> None:
        self.blank_count += 1
        if len(self.blank_rows) < BLANK_ROWS_KEPT:
            self.blank_rows.append(row)


def _lines(payload: bytes) -> Iterator[str]:
    text = io.TextIOWrapper(io.BytesIO(payload), encoding="utf-8", errors="strict", newline="")
    return (line.replace("\ufeff", "") for line in text)


def _blank(record: list[str]) -> bool:
    return all(not value.strip() for value in record)


class CsvFile:
    """An upload's header, then its data records, read lazily and bounded.

    ``max_columns`` bounds the header; ``max_rows`` the non-blank data rows. A
    breach of either, a malformed record or bytes that are not UTF-8 raise
    ``SchemaValidationError`` - the whole file is refused, as V1 refuses it.
    """

    def __init__(self, payload: bytes, *, max_columns: int, max_rows: int) -> None:
        if b"\x00" in payload:
            raise SchemaValidationError("file contains NUL bytes; it is not a text CSV file")
        self._max_columns = max_columns
        self._max_rows = max_rows
        self._reader = csv.reader(_lines(payload), strict=True)
        self._row = 0
        self.tally = Tally()

    def _next(self) -> list[str] | None:
        try:
            record = next(self._reader)
        except StopIteration:
            return None
        except UnicodeDecodeError:
            raise SchemaValidationError("file is not UTF-8 text; save it as CSV (UTF-8)") from None
        except csv.Error as exc:
            # The class only: the module's text can quote the file.
            logger.info("v2 import preview: unreadable CSV (%s)", exc.__class__.__name__)
            if "field limit" in str(exc):
                raise SchemaValidationError(
                    f"file has a field longer than {csv.field_size_limit():,} characters"
                ) from None
            raise SchemaValidationError(UNREADABLE.format(row=self._row + 1)) from None
        self._row += 1
        return record

    def header(self) -> Header:
        while (record := self._next()) is not None:
            if _blank(record):
                self.tally.blank(self._row)
                continue
            if len(record) == 1 and ";" in record[0]:
                raise SchemaValidationError(
                    "file looks semicolon-separated; save it as comma-separated CSV (UTF-8)"
                )
            if len(record) > self._max_columns:
                raise SchemaValidationError(
                    f"file has {len(record):,} columns; the limit is {self._max_columns:,}"
                )
            return Header(record, self._row)
        raise SchemaValidationError("file is empty")

    def records(self) -> Iterator[Record]:
        """The data rows after the header; blank ones are tallied, not yielded."""
        while (record := self._next()) is not None:
            if _blank(record):
                self.tally.blank(self._row)
                continue
            self.tally.data_rows += 1
            if self.tally.data_rows > self._max_rows:
                raise SchemaValidationError(
                    f"file has more than {self._max_rows:,} data rows; "
                    f"split it into files of at most {self._max_rows:,} rows"
                )
            yield Record(self._row, record)
