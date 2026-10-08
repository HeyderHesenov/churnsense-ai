"""Exception types for ChurnSense.

Each exception marks a *boundary* where a failure becomes meaningful to the
caller: bad configuration, a dataset that violates its contract, a missing
model artifact, or invalid user input. Internal control flow does not raise
these -- they exist so that the API and dashboard can map a failure to the
right user-facing message without inspecting strings.
"""


class ChurnSenseError(Exception):
    """Base class for every error this package raises deliberately."""


class ConfigError(ChurnSenseError):
    """The configuration file is missing, malformed, or internally inconsistent."""


class DataError(ChurnSenseError):
    """The dataset could not be obtained, or violates the expected schema."""


class SchemaValidationError(DataError):
    """Input data failed column, dtype, or category validation.

    Carries the individual problems so callers can show all of them at once
    instead of making the user fix one error per attempt.
    """

    def __init__(self, message: str, problems: list[str] | None = None) -> None:
        self.problems: list[str] = problems or []
        detail = "; ".join(self.problems)
        super().__init__(f"{message}: {detail}" if detail else message)


class ModelNotAvailableError(ChurnSenseError):
    """No usable trained artifact exists.

    Raised instead of ever returning a placeholder or fabricated prediction.
    The API maps this to HTTP 503 and the dashboard to a "run `make train`"
    state.
    """
