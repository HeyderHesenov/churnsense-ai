"""What the Data Hub refuses to carry: direct personal identifiers.

The contract is about customer *companies*. A column that names, reaches or
locates a person - an email address, a phone number, a contact's name, a postal
address - is never mapped, never parsed and never returned: only its name is
reported, so the user knows it was dropped. Two checks, both deterministic:

* ``sensitive_header`` reads a column's *name*, split into words
  (``contactEmail``, ``Contact E-mail`` and ``contact_email`` alike).
* ``looks_like_personal_identifier`` reads a *value* of the two columns that
  identify an account (``account_external_id``, ``account_name``): an email
  address anywhere in it, or a whole value that is a phone number.

Neither is a guarantee. A header can hide an email address under any name, and
a phone number written as a bare run of digits is indistinguishable from a
numeric account ID, so only the forms below are recognised. They narrow what
reaches the service; they do not replace the rule that only synthetic or test
data is uploaded to the preview.
"""

from __future__ import annotations

import re
from itertools import pairwise
from typing import Final

#: Words that make a column personal on their own.
_SENSITIVE_WORDS: Final = frozenset(
    {
        "mail",
        "email",
        "emails",
        "phone",
        "phones",
        "mobile",
        "cell",
        "cellphone",
        "tel",
        "telephone",
        "fax",
        "msisdn",
        "whatsapp",
        "sms",
        "ssn",
        "passport",
        "birth",
        "birthday",
        "birthdate",
        "dob",
        "address",
        "street",
        "postcode",
        "zipcode",
        "username",
    }
)
#: Parts of a word that make it personal wherever they appear (``workemail``, ``smartphone``).
_SENSITIVE_PARTS: Final = ("email", "phone")
#: Adjacent words that name a person rather than a company.
_SENSITIVE_PAIRS: Final = frozenset(
    {
        ("first", "name"),
        ("last", "name"),
        ("full", "name"),
        ("given", "name"),
        ("family", "name"),
        ("contact", "name"),
        ("contact", "person"),
        ("person", "name"),
        ("user", "name"),
        ("national", "id"),
        ("postal", "code"),
        ("zip", "code"),
    }
)

_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Za-z])(?=[0-9])")
_SEPARATORS = re.compile(r"[^a-z0-9]+")

#: An email address anywhere in a value: something@something.tld.
_EMAIL = re.compile(r"[^\s@]+@[^\s@]+\.[^\s@]+")
#: A whole value that is an international number: +, then 7 to 15 digits, with
#: spaces, dots, dashes or brackets between them.
_INTERNATIONAL_PHONE = re.compile(r"\+[\s().-]*(?:\d[\s().-]*){7,15}")
#: A whole value in the North American form: (555) 123-4567, 555-123-4567, 555.123.4567.
_GROUPED_PHONE = re.compile(r"\(?\d{3}\)?[\s.-]+\d{3}[\s.-]\d{4}")

#: How much of a column name a response repeats.
NAME_DISPLAY_LENGTH: Final = 64


def words(header: str) -> list[str]:
    """A column name as lowercase words: ``contactEmail`` and ``Contact E-mail`` alike."""
    spaced = _CAMEL.sub("_", header.strip())
    return [w for w in _SEPARATORS.split(spaced.lower()) if w]


def normalise(header: str) -> str:
    """The name a column is matched under: lowercase words joined by ``_``."""
    return "_".join(words(header))


def sensitive_header(header: str) -> bool:
    """Whether a column's name says it holds a direct personal identifier.

    ``e-mail`` splits into ``e`` and ``mail``, so ``mail`` alone is enough.
    """
    parts = words(header)
    if any(w in _SENSITIVE_WORDS or any(p in w for p in _SENSITIVE_PARTS) for w in parts):
        return True
    return any(pair in _SENSITIVE_PAIRS for pair in pairwise(parts))


def looks_like_personal_identifier(value: str) -> bool:
    """Whether a value is, or contains, an email address, or is a phone number."""
    text = value.strip()
    return bool(
        _EMAIL.search(text)
        or _INTERNATIONAL_PHONE.fullmatch(text)
        or _GROUPED_PHONE.fullmatch(text)
    )


def display_name(header: str, position: int) -> str:
    """A column name as a response may repeat it.

    A file without a header row has a customer's values for column names, so a
    name that is itself an email address or a phone number is never repeated:
    the column is named by its position instead. Long names are cut short.
    """
    text = header.strip()
    if not text or looks_like_personal_identifier(text):
        return f"[column {position}]"
    if len(text) > NAME_DISPLAY_LENGTH:
        return text[: NAME_DISPLAY_LENGTH - 1] + "…"
    return text
