/**
 * The V2 Preview's requests: the Data Hub preview (M1) and the Retention
 * Workbench's three reads (M2), nothing else. Typed by the schema the API
 * itself exports with the preview on (``npm run openapi:v2``), and sent through
 * the app's one client (``api/client``), so errors, aborts and the same-origin
 * rules are the V1 pages' own.
 *
 * Every figure is the API's. Nothing here or in a V2 page computes an amount,
 * a level, an order or a summary.
 */
import { keepPreviousData, skipToken, useQuery, type UseQueryResult } from '@tanstack/react-query';
import { request } from '../api/client';
import type { components, operations } from '../api/v2.schema';

type Schemas = components['schemas'];

export type ImportPreview = Schemas['ImportPreviewResponse'];
export type AccountSnapshot = Schemas['AccountSnapshot'];
export type ColumnRef = Schemas['ColumnRef'];
export type RowIssue = Schemas['RowIssue'];
export type PortfolioSummary = Schemas['PortfolioSummary'];
export type CurrencySummary = Schemas['CurrencySummary'];
export type DatasetInfo = Schemas['DatasetInfo'];
export type AccountPage = Schemas['AccountPage'];
export type AccountRow = Schemas['AccountRow'];
export type AccountDetail = Schemas['AccountDetail'];
export type Assessment = Schemas['Assessment'];
export type Indicator = Schemas['Indicator'];
export type HistoryEntry = Schemas['HistoryEntry'];

export type AccountStatus = AccountRow['status'];
export type Level = NonNullable<Assessment['level']>;
export type LevelFilter = Schemas['AppliedFilters']['level'][number];
export type SortKey = AccountPage['sort'];
export type PreviewStatus = ImportPreview['status'];

/** The list's query string, exactly as the API declares it. */
export type AccountQuery = NonNullable<
  operations['accounts_api_v2_accounts_get']['parameters']['query']
>;

export const PREVIEW_PATH = '/v2/imports/preview';
export const SUMMARY_PATH = '/v2/portfolio/summary';
export const ACCOUNTS_PATH = '/v2/accounts';

/** Every V2 path this front end requests: the M1 and M2 routes, and no other. */
export const V2_API_PATHS = [PREVIEW_PATH, SUMMARY_PATH, ACCOUNTS_PATH] as const;

export const v2Keys = {
  summary: ['v2', 'portfolio-summary'] as const,
  accounts: (query: AccountQuery) => ['v2', 'accounts', query] as const,
  account: (id: string) => ['v2', 'account', id] as const,
};

export function usePortfolioSummary(): UseQueryResult<PortfolioSummary> {
  return useQuery({
    queryKey: v2Keys.summary,
    queryFn: ({ signal }) => request<PortfolioSummary>(SUMMARY_PATH, { signal }),
  });
}

/** One page of the list; the page shown stays while the next one loads. */
export function useAccounts(query: AccountQuery): UseQueryResult<AccountPage> {
  return useQuery({
    queryKey: v2Keys.accounts(query),
    queryFn: ({ signal }) => request<AccountPage>(ACCOUNTS_PATH, { query, signal }),
    placeholderData: keepPreviousData,
  });
}

/** One account, or nothing asked while no ID is given. */
export function useAccount(id: string | null): UseQueryResult<AccountDetail> {
  return useQuery({
    queryKey: v2Keys.account(id ?? ''),
    queryFn:
      id === null
        ? skipToken
        : ({ signal }) =>
            request<AccountDetail>(`${ACCOUNTS_PATH}/${encodeURIComponent(id)}`, { signal }),
  });
}

/** A mapping as the reader made it: ``[source, target]`` pairs, in order. */
export type Pairs = readonly (readonly [string, string])[];

export interface PreviewRequest {
  file: File;
  /** Column name -> canonical field. When sent, complete and the only mapping used. */
  mapping?: Pairs | undefined;
  /** Source status value -> canonical status; left out when empty. */
  statusMapping?: Pairs | undefined;
}

/**
 * ``pairs`` as a JSON object, written pair by pair: a name given twice stays
 * twice, so the server - which refuses a repeated key - says so, rather than
 * the browser keeping the last one unseen.
 */
export function pairsJson(pairs: Pairs): string {
  return `{${pairs.map(([key, value]) => `${JSON.stringify(key)}:${JSON.stringify(value)}`).join(',')}}`;
}

/**
 * Ask the server what ``file`` would import. The file is sent once, checked in
 * memory and discarded with the response: nothing is stored or committed.
 */
export function previewImport(
  { file, mapping, statusMapping }: PreviewRequest,
  signal: AbortSignal,
): Promise<ImportPreview> {
  const form = new FormData();
  form.append('file', file, file.name);
  if (mapping) form.append('mapping', pairsJson(mapping));
  if (statusMapping && statusMapping.length > 0) {
    form.append('status_mapping', pairsJson(statusMapping));
  }
  return request<ImportPreview>(PREVIEW_PATH, { method: 'POST', form, signal });
}
