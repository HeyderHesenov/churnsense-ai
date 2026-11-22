"""Customer Intelligence and the Retention Workbench over HTTP: three reads of synthetic data.

* ``GET /api/v2/portfolio/summary`` - counts and per-currency MRR, computed here.
* ``GET /api/v2/accounts`` - the prioritised account list, filtered, sorted, paged.
* ``GET /api/v2/accounts/{account_id}`` - one account, its evidence and history.

**The Data Hub preview's gate, and no other.** ``churnsense.workspace.api.install``
adds these routes with the preview, under the same rule: the environment sets
``CHURNSENSE_V2_DATA_HUB_PREVIEW=1`` and the deployment is ``local``. A public
demo never has them, and each request checks the mode again, as the preview does.

**Synthetic, stateless.** Every request generates the documented dataset
(``synthetic``) and discards it with the response. Nothing an upload to the
preview contained is here: the preview keeps nothing to give.

A query parameter the route does not take, or a single-valued one given twice,
is refused with a 422 rather than ignored, so a misspelt filter never returns an
unfiltered list that looks filtered.
"""

from __future__ import annotations

from typing import Annotated, Final

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi import Path as PathParam

from churnsense.api.schemas import ErrorResponse
from churnsense.exceptions import SchemaValidationError
from churnsense.workspace import contract, synthetic, workbench
from churnsense.workspace import workbench_schemas as out
from churnsense.workspace.api import _local_only
from churnsense.workspace.schemas import AccountStatus

SUMMARY_PATH: Final = "/portfolio/summary"
ACCOUNTS_PATH: Final = "/accounts"
#: The list's query parameters; those that take one value, and those that may repeat.
LIST_SCALARS: Final = frozenset(
    {"currency", "segment", "renewal_within_days", "q", "sort", "page", "page_size"}
)
LIST_REPEATABLE: Final = frozenset({"level", "status"})
#: Search text: printable, bounded.
_SEARCH = r"^[^\x00-\x1f\x7f]+$"
_ERRORS: Final = {status: {"model": ErrorResponse} for status in (400, 403, 404, 422, 429, 500)}


def _known_parameters(allowed: frozenset[str], repeatable: frozenset[str] = frozenset()):
    def check(request: Request) -> None:
        params = request.query_params
        if unknown := sorted(set(params.keys()) - allowed - repeatable):
            raise SchemaValidationError(
                f"unknown query parameter(s): {len(unknown)}; this route takes "
                f"{sorted(allowed | repeatable) or 'none'}"
            )
        if repeated := sorted(k for k in allowed if len(params.getlist(k)) > 1):
            raise SchemaValidationError(
                f"query parameter(s) {repeated} take one value each, not several"
            )

    return check


_no_parameters = _known_parameters(frozenset())


def _book() -> workbench.Book:
    """The request's own dataset: generated once, used by this request only."""
    return workbench.load_book()


router = APIRouter(tags=["v2 retention workbench"], dependencies=[Depends(_local_only)])


@router.get(
    SUMMARY_PATH,
    response_model=out.PortfolioSummary,
    responses=_ERRORS,
    dependencies=[Depends(_no_parameters)],
)
def portfolio_summary(
    book: Annotated[workbench.Book, Depends(_book)],
) -> out.PortfolioSummary:
    """Account counts by rule level and status, and MRR per currency, of the synthetic book.

    Every amount is per currency; no total adds two. Exposed MRR is the gross MRR of
    accounts with attention or watch signals - not a forecast of lost or saved revenue.
    """
    return workbench.portfolio_summary(book)


@router.get(
    ACCOUNTS_PATH,
    response_model=out.AccountPage,
    responses=_ERRORS,
    dependencies=[Depends(_known_parameters(LIST_SCALARS, LIST_REPEATABLE))],
)
def accounts(
    book: Annotated[workbench.Book, Depends(_book)],
    level: Annotated[list[out.LevelFilter] | None, Query()] = None,
    status: Annotated[list[AccountStatus] | None, Query()] = None,
    currency: Annotated[str | None, Query(pattern=r"^[A-Z]{3}$")] = None,
    segment: Annotated[
        str | None, Query(min_length=1, max_length=contract.LABEL_MAX_LENGTH, pattern=_SEARCH)
    ] = None,
    renewal_within_days: Annotated[int | None, Query(ge=0, le=366)] = None,
    q: Annotated[str | None, Query(min_length=1, max_length=100, pattern=_SEARCH)] = None,
    sort: out.SortKey = "priority",
    page: Annotated[int, Query(ge=1, le=10_000)] = 1,
    page_size: Annotated[
        int, Query(ge=1, le=workbench.MAX_PAGE_SIZE)
    ] = workbench.DEFAULT_PAGE_SIZE,
) -> out.AccountPage:
    """The accounts the rules point at first: attention, then watch, then no signals.

    ``priority`` (the default) orders by level, then the nearest renewal, then the
    account ID; ``renewal_asc`` by the nearest renewal; ``mrr_desc`` by MRR, and only
    within one ``currency``. ``renewal_within_days`` keeps assessed accounts renewing
    within that many days. ``q`` searches the account ID and name. A page past the
    end is the last page.
    """
    filters = workbench.Filters(
        level=workbench.level_filter(level or ()),
        status=workbench.status_filter(status or ()),
        currency=currency,
        segment=segment,
        renewal_within_days=renewal_within_days,
        q=q,
    )
    return workbench.account_page(book, filters, sort, page, page_size)


@router.get(
    f"{ACCOUNTS_PATH}/{{account_id}}",
    response_model=out.AccountDetail,
    responses=_ERRORS,
    dependencies=[Depends(_no_parameters)],
)
def account(
    account_id: Annotated[str, PathParam(min_length=1, max_length=contract.ID_MAX_LENGTH)],
) -> out.AccountDetail:
    """One synthetic account: its latest snapshot, rule assessment, evidence and history.

    Each historical assessment reads only that snapshot and earlier ones.
    """
    found = synthetic.find(account_id)
    if found is None:
        raise HTTPException(status_code=404, detail="No account with that ID.")
    return workbench.account_detail(found, workbench.dataset_info())
