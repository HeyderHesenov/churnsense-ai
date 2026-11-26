"""Which column of an upload feeds which canonical field - decided, never guessed.

Two ways a column is matched, both deterministic:

* **exact** - its name, surrounding spaces trimmed, is the field's name;
* **normalised** - its name as lowercase words joined by ``_`` is the field's
  name (``Account External ID``, ``accountExternalId`` and ``account-external-id``
  all read ``account_external_id``).

Nothing fuzzier: no edit distance, no synonyms, no model. A field two columns
match is *ambiguous* and is not chosen, whichever of the two matches better.

Without an explicit mapping every unambiguous match is applied, and the response
says so (``source: suggested``). An explicit mapping is complete and the only
one used: a column it does not name is ignored, whatever its name suggests. It
is refused - the whole request, 422 - when it names a column the file does not
have, a column whose name appears twice, a column holding personal identifiers,
a field the contract does not have, or one field twice, or leaves a required
field out.

A column whose name says it holds a direct personal identifier
(``churnsense.workspace.privacy``) is dropped before any of this and can be
mapped by neither route.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from typing import Any, Final, Literal

from churnsense.exceptions import SchemaValidationError
from churnsense.workspace import contract, privacy

Match = Literal["explicit", "exact", "normalised"]

#: The longest source name or value a mapping may hold.
MAPPING_KEY_LENGTH: Final = 256
#: The most source values a status mapping may translate.
STATUS_MAPPING_ENTRIES: Final = 100


@dataclass(frozen=True, slots=True)
class Column:
    """One column of the upload: its 1-based position, its name, and how it may be shown."""

    position: int
    header: str
    name: str

    @property
    def index(self) -> int:
        return self.position - 1


@dataclass(frozen=True, slots=True)
class Assignment:
    column: Column
    target: str
    match: Match


@dataclass(frozen=True, slots=True)
class Unresolved:
    """A field no column was assigned to: none matches it, or more than one does."""

    target: str
    required: bool
    reason: Literal["missing", "ambiguous"]
    candidates: tuple[Column, ...] = ()


@dataclass(frozen=True, slots=True)
class Mapping:
    source: Literal["explicit", "suggested"]
    applied: tuple[Assignment, ...]
    suggestions: tuple[Assignment, ...]
    unresolved: tuple[Unresolved, ...]
    ignored: tuple[Column, ...]
    dropped: tuple[Column, ...]

    @property
    def targets(self) -> dict[str, Column]:
        return {a.target: a.column for a in self.applied}

    @property
    def missing_required(self) -> tuple[str, ...]:
        mapped = self.targets
        return tuple(name for name in contract.REQUIRED if name not in mapped)


def columns_of(header: list[str]) -> list[Column]:
    return [
        Column(position, text, privacy.display_name(text, position))
        for position, text in enumerate(header, start=1)
    ]


def _match(header: str) -> tuple[str, Match] | None:
    text = header.strip()
    if text in contract.BY_NAME:
        return text, "exact"
    normalised = privacy.normalise(text)
    if normalised in contract.BY_NAME:
        return normalised, "normalised"
    return None


def _split(columns: list[Column]) -> tuple[list[Column], list[Column]]:
    """The columns that may be mapped, and those dropped as personal."""
    kept = [c for c in columns if not privacy.sensitive_header(c.header)]
    dropped = [c for c in columns if privacy.sensitive_header(c.header)]
    return kept, dropped


def _suggestions(kept: list[Column]) -> tuple[list[Assignment], list[Unresolved], list[Column]]:
    """Unambiguous matches, the fields left unresolved, and the columns nothing matches."""
    candidates: dict[str, list[tuple[Column, Match]]] = {}
    unknown: list[Column] = []
    for column in kept:
        if (found := _match(column.header)) is None:
            unknown.append(column)
        else:
            candidates.setdefault(found[0], []).append((column, found[1]))
    suggestions, unresolved = [], []
    for field in contract.FIELDS:
        matched = candidates.get(field.name, [])
        if len(matched) == 1:
            suggestions.append(Assignment(matched[0][0], field.name, matched[0][1]))
        elif matched:
            columns = tuple(column for column, _ in matched)
            unresolved.append(Unresolved(field.name, field.required, "ambiguous", columns))
        elif field.required:
            unresolved.append(Unresolved(field.name, True, "missing"))
    suggestions.sort(key=lambda a: a.column.position)
    return suggestions, unresolved, unknown


def resolve(header: list[str], explicit: dict[str, str] | None) -> Mapping:
    """The mapping a file is read with: ``explicit`` if given, otherwise the suggestions.

    ``ignored`` is every column no field is read from: unknown ones, ones an
    explicit mapping leaves out, and ambiguous ones (also under ``unresolved``).
    """
    kept, dropped = _split(columns_of(header))
    suggestions, unresolved, _ = _suggestions(kept)
    if explicit is None:
        source, applied, unresolved_kept = "suggested", suggestions, unresolved
    else:
        source, applied, unresolved_kept = "explicit", _explicit(explicit, kept, dropped), []
    used = {a.column.position for a in applied}
    return Mapping(
        source,
        tuple(sorted(applied, key=lambda a: a.column.position)),
        tuple(suggestions),
        tuple(unresolved_kept),
        tuple(c for c in kept if c.position not in used),
        tuple(dropped),
    )


def _shown(name: str) -> str:
    """A client-sent name, as an error message may repeat it."""
    return privacy.display_name(name, 0) if name.strip() else "(blank)"


def _explicit(
    explicit: dict[str, str], kept: list[Column], dropped: list[Column]
) -> list[Assignment]:
    """``explicit`` checked against the file and the contract; every problem, or the assignments."""
    by_name: dict[str, list[Column]] = {}
    for column in kept:
        by_name.setdefault(column.header.strip(), []).append(column)
    personal = {c.header.strip() for c in dropped}
    problems: list[str] = []
    applied: list[Assignment] = []
    for source, target in explicit.items():
        name = source.strip()
        if target not in contract.BY_NAME:
            problems.append(f"'{_shown(source)}' is mapped to an unknown field")
        if name in personal:
            problems.append(
                f"'{_shown(source)}' holds personal identifiers and cannot be mapped to any field"
            )
            continue
        found = by_name.get(name, [])
        if not found:
            problems.append(f"'{_shown(source)}' is not a column of the file")
        elif len(found) > 1:
            problems.append(
                f"'{_shown(source)}' names {len(found)} columns of the file; rename them apart"
            )
        elif target in contract.BY_NAME:
            applied.append(Assignment(found[0], target, "explicit"))
    sources = Counter(source.strip() for source in explicit)
    for name in sorted(n for n, count in sources.items() if count > 1):
        problems.append(f"'{_shown(name)}' is mapped more than once")
    targets = Counter(explicit.values())
    for target in sorted(t for t, count in targets.items() if count > 1 and t in contract.BY_NAME):
        problems.append(f"field '{target}' is mapped from more than one column")
    mapped = set(explicit.values())
    if missing := [name for name in contract.REQUIRED if name not in mapped]:
        problems.append(f"required field(s) not mapped: {', '.join(missing)}")
    if problems:
        raise SchemaValidationError("the column mapping is invalid", problems)
    return applied


class _DuplicateKeyError(ValueError):
    pass


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    keys = [key for key, _ in pairs]
    if len(set(keys)) != len(keys):
        raise _DuplicateKeyError
    return dict(pairs)


def _object(text: str, what: str, max_entries: int) -> dict[str, str]:
    """A JSON object of strings to strings; a duplicate key is refused, never resolved."""
    try:
        value = json.loads(text, object_pairs_hook=_pairs)
    except _DuplicateKeyError:
        raise SchemaValidationError(f"{what} names the same key more than once") from None
    except (ValueError, RecursionError):
        raise SchemaValidationError(f"{what} is not valid JSON") from None
    if not isinstance(value, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in value.items()
    ):
        raise SchemaValidationError(f"{what} must be a JSON object of strings to strings")
    if len(value) > max_entries:
        raise SchemaValidationError(f"{what} has more than {max_entries:,} entries")
    if any(len(k) > MAPPING_KEY_LENGTH or len(v) > MAPPING_KEY_LENGTH for k, v in value.items()):
        raise SchemaValidationError(
            f"{what} has a key or value over {MAPPING_KEY_LENGTH} characters"
        )
    return value


def parse_mapping(text: str | None, max_entries: int) -> dict[str, str] | None:
    """The explicit column mapping a request sent, ``{"source column": "field"}``, or None."""
    if text is None:
        return None
    return _object(text, "mapping", max_entries)


def parse_status_mapping(text: str | None) -> dict[str, str]:
    """Source status values translated to canonical ones, ``{"source value": "status"}``.

    Keys are matched against a cell's trimmed value exactly, case included. A
    key that is itself a canonical status in any case is refused: those are read
    as they are, and translating one into another would be a silent rewrite.
    """
    if text is None:
        return {}
    raw = _object(text, "status_mapping", STATUS_MAPPING_ENTRIES)
    problems: list[str] = []
    translated: dict[str, str] = {}
    canonical = {s.casefold() for s in contract.STATUSES}
    for key, value in raw.items():
        source = key.strip()
        if not source:
            problems.append("status_mapping has a blank source value")
        elif source.casefold() in canonical:
            problems.append("status_mapping cannot translate a canonical status")
        elif source in translated:
            problems.append("status_mapping names the same source value more than once")
        if value not in contract.STATUSES:
            problems.append(
                f"status_mapping translates to a value that is not one of {list(contract.STATUSES)}"
            )
        translated[source] = value
    if problems:
        raise SchemaValidationError("the status mapping is invalid", sorted(set(problems)))
    return translated
