/**
 * One hook per endpoint, the batch upload aside (``scoreBatch``). The book
 * does not change while it is served, so what is fetched once stays fresh for
 * the session; a filter or an assumption is part of the key, and a superseded
 * request is aborted (React Query passes the AbortSignal through to fetch).
 */
import {
  keepPreviousData,
  QueryClient,
  queryOptions,
  skipToken,
  useQuery,
  type UseQueryResult,
} from '@tanstack/react-query';
import { ApiError, request, type Schemas } from './client';

export type ModelInfo = Schemas['ModelInfoResponse'];
export type DataSource = Schemas['DataSource'];
export type Overview = Schemas['OverviewResponse'];
export type CustomerPage = Schemas['CustomerPage'];
export type CustomerRow = Schemas['CustomerRow'];
export type CustomerDetail = Schemas['CustomerDetail'];
export type CustomerFeatures = Schemas['CustomerFeatures'];
export type Examples = Schemas['ExamplesResponse'];
export type ExplainResponse = Schemas['ExplainResponse'];
export type Explanation = Schemas['Explanation'];
export type Contribution = Schemas['Contribution'];
export type Segments = Schemas['SegmentsResponse'];
export type Cell = Schemas['ReportedCell'] | Schemas['SuppressedCell'];
export type GlobalExplanation = Schemas['GlobalExplanation'];
export type Sweep = Schemas['SweepResponse'];
export type SweepPoint = Schemas['SweepPoint'];
export type Performance = Schemas['PerformanceResponse'];
export type Prediction = Schemas['PredictionResponse'];
export type BatchScore = Schemas['BatchScoreResponse'];
export type BandSummary = Schemas['BandSummary'];
export type Health = Schemas['HealthResponse'];

/** Retry a failure of the server or the network, once; never a refusal (4xx). */
export function shouldRetry(failures: number, error: unknown): boolean {
  return error instanceof ApiError && error.transient && failures < 1;
}

/** Wait as long as the server asks, but never more than ten seconds; else back off from 0.5 s. */
export function retryDelay(attempt: number, error: unknown): number {
  if (error instanceof ApiError && error.retryAfter !== null) {
    return Math.min(error.retryAfter, 10) * 1000;
  }
  return Math.min(4000, 500 * 2 ** attempt);
}

export function createQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: Infinity,
        gcTime: 10 * 60_000,
        refetchOnWindowFocus: false,
        retry: shouldRetry,
        retryDelay,
      },
    },
  });
}

export function useHealth(): UseQueryResult<Health> {
  return useQuery({
    queryKey: ['health'],
    queryFn: ({ signal }) => request<Health>('/health', { signal }),
    staleTime: 30_000,
  });
}

export function useModelInfo(): UseQueryResult<ModelInfo> {
  return useQuery({
    queryKey: ['model-info'],
    queryFn: ({ signal }) => request<ModelInfo>('/model-info', { signal }),
  });
}

export function useOverview(): UseQueryResult<Overview> {
  return useQuery({
    queryKey: ['overview'],
    queryFn: ({ signal }) => request<Overview>('/overview', { signal }),
  });
}

export type SortKey = 'id' | 'probability' | 'tenure' | 'monthly' | 'at_risk';
export type Direction = 'asc' | 'desc';
/** `synthetic`: a public demo's generated customer, in no partition of any dataset. */
export type Partition = 'train' | 'validation' | 'test' | 'unknown' | 'synthetic';

export interface CustomerFilter {
  q?: string;
  bands?: readonly string[];
  contract?: string | null;
  internet?: string | null;
  payment?: string | null;
  tenure_min?: number | null;
  tenure_max?: number | null;
  flagged?: boolean | null;
  partition?: Partition | null;
  /** Leave out customers recorded as churned; a withheld outcome is not one of them. */
  exclude_churned?: boolean;
  /** Exactly these customers (at most 200); the API refuses ids with any other filter. */
  ids?: readonly string[];
}

export interface CustomerQuery extends CustomerFilter {
  sort: SortKey;
  dir: Direction;
  page: number;
  page_size: number;
}

/** The query string an API listing takes; empty filters are left out. */
export function customerParams(query: CustomerFilter & Partial<CustomerQuery>) {
  return {
    q: query.q || undefined,
    bands: query.bands,
    contract: query.contract,
    internet: query.internet,
    payment: query.payment,
    tenure_min: query.tenure_min,
    tenure_max: query.tenure_max,
    flagged: query.flagged,
    partition: query.partition,
    exclude_churned: query.exclude_churned,
    ids: query.ids,
    sort: query.sort,
    dir: query.dir,
    page: query.page,
    page_size: query.page_size,
  };
}

/**
 * An empty band list chooses no band, and an empty id list no customer. The
 * API reads a missing ``bands`` or ``ids`` as "any", so such a choice is never
 * sent: the query stays idle and the page says nothing is chosen. A tenure
 * range whose minimum is above its maximum holds nobody (the API refuses it),
 * so it is never sent either.
 */
export function choosesSomething(filter: CustomerFilter): boolean {
  const { tenure_min: min, tenure_max: max } = filter;
  const emptyRange =
    min !== null && min !== undefined && max !== null && max !== undefined && min > max;
  return filter.bands?.length !== 0 && filter.ids?.length !== 0 && !emptyRange;
}

/**
 * A listing's key and request, shared by the hook and a page that fetches one
 * ahead. A query that chooses nothing has no request at all, so fetching it
 * ahead fails rather than asks for every customer.
 */
export function customersQuery(query: CustomerQuery) {
  return queryOptions({
    queryKey: ['customers', query],
    queryFn: choosesSomething(query)
      ? ({ signal }) =>
          request<CustomerPage>('/customers', { query: customerParams(query), signal })
      : skipToken,
  });
}

export function useCustomers(query: CustomerQuery): UseQueryResult<CustomerPage> {
  return useQuery({ ...customersQuery(query), placeholderData: keepPreviousData });
}

/**
 * What a filter keeps, whatever the page, sort or page size: its count, its
 * summary of every match and whether outcomes are withheld (one row asked for).
 */
function selectionQuery(filter: CustomerFilter) {
  return queryOptions({
    queryKey: ['customers', 'selection', filter],
    queryFn: choosesSomething(filter)
      ? ({ signal }) =>
          request<CustomerPage>('/customers', {
            query: customerParams({ ...filter, page_size: 1 }),
            signal,
          })
      : skipToken,
  });
}

export function useSelection(filter: CustomerFilter): UseQueryResult<CustomerPage> {
  return useQuery(selectionQuery(filter));
}

/** How many customers a filter keeps, for a preset's count (the selection's own request). */
export function useCustomerCount(filter: CustomerFilter): UseQueryResult<number> {
  return useQuery({ ...selectionQuery(filter), select: (page) => page.total });
}

/** A customer ID as the API accepts it; anything else (".." included) is never put in a path. */
const CUSTOMER_ID = /^[A-Za-z0-9-]{1,32}$/;

export function useCustomer(id: string | null): UseQueryResult<CustomerDetail> {
  const valid = id !== null && CUSTOMER_ID.test(id);
  return useQuery({
    queryKey: ['customer', id],
    queryFn: ({ signal }) => request<CustomerDetail>(`/customers/${id ?? ''}`, { signal }),
    enabled: valid,
  });
}

export function useExamples(): UseQueryResult<Examples> {
  return useQuery({
    queryKey: ['examples'],
    queryFn: ({ signal }) => request<Examples>('/customers/examples', { signal }),
  });
}

/** Explanations of book customers; nothing is fetched for an empty list. */
export function useExplanations(ids: readonly string[]): UseQueryResult<ExplainResponse> {
  return useQuery({
    queryKey: ['explain', 'ids', ids],
    queryFn: ({ signal }) =>
      request<ExplainResponse>('/explain', {
        method: 'POST',
        json: { customer_ids: ids },
        signal,
      }),
    enabled: ids.length > 0,
    retry: (failures, error: Error) => retryExplanation(failures, error),
  });
}

/**
 * Explanations are asked for as the reader moves (a page of the queue, a
 * customer, a typed record), so the rate limit can refuse one: it is asked for
 * once more, after the wait the server names. One the server can never give
 * (no verified background) is not.
 */
export function retryExplanation(failures: number, error: unknown): boolean {
  if (!(error instanceof ApiError) || error.code === 'explanations_unavailable') return false;
  return failures < 1 && (error.transient || error.status === 429);
}

/** The explanation of one record that is not in the book. */
export function useRecordExplanation(
  record: CustomerFeatures | null,
): UseQueryResult<ExplainResponse> {
  return useQuery({
    queryKey: ['explain', 'record', record],
    queryFn: ({ signal }) =>
      request<ExplainResponse>('/explain', { method: 'POST', json: { record }, signal }),
    enabled: record !== null,
    placeholderData: keepPreviousData,
    retry: (failures, error: Error) => retryExplanation(failures, error),
  });
}

/** ``POST /api/predict``: the served prediction for one record. */
export function usePrediction(record: CustomerFeatures | null): UseQueryResult<Prediction> {
  return useQuery({
    queryKey: ['predict', record],
    queryFn: ({ signal }) =>
      request<Prediction>('/predict', { method: 'POST', json: record, signal }),
    enabled: record !== null,
    placeholderData: keepPreviousData,
  });
}

/** A breakdown of the training partition's observed churn; the last one stays while the next loads. */
export function useSegments(rows: string, cols: string | null): UseQueryResult<Segments> {
  return useQuery({
    queryKey: ['segments', rows, cols],
    queryFn: ({ signal }) => request<Segments>('/segments', { query: { rows, cols }, signal }),
    placeholderData: keepPreviousData,
    retry: (failures, error: Error) => retryOutcomes(failures, error),
  });
}

/**
 * The cached attributions are asked for again once when the server or the
 * network failed; never when none are cached, which stays so until a
 * maintainer runs ``make explain``.
 */
export function retryGlobal(failures: number, error: unknown): boolean {
  if (error instanceof ApiError && error.code === 'explanations_not_cached') return false;
  return shouldRetry(failures, error);
}

export function useGlobalExplanation(): UseQueryResult<GlobalExplanation> {
  return useQuery({
    queryKey: ['explain', 'global'],
    queryFn: ({ signal }) => request<GlobalExplanation>('/explain/global', { signal }),
    retry: (failures, error: Error) => retryGlobal(failures, error),
  });
}

export interface Assumptions {
  cost: number;
  success: number;
  horizon: number;
  margin: number;
}

/**
 * Figures that rest on observed outcomes (a sweep, a breakdown) are asked for
 * again once when the server or the network failed; never when the server
 * withholds the outcomes, which it does for as long as it runs.
 */
export function retryOutcomes(failures: number, error: unknown): boolean {
  if (error instanceof ApiError && error.code === 'outcomes_withheld') return false;
  return shouldRetry(failures, error);
}

/** The validation sweep under ``assumptions``; nothing is asked for while they are ``null``. */
export function useSweep(assumptions: Assumptions | null): UseQueryResult<Sweep> {
  return useQuery({
    queryKey: ['sweep', assumptions],
    queryFn:
      assumptions === null
        ? skipToken
        : ({ signal }) => request<Sweep>('/threshold/sweep', { query: { ...assumptions }, signal }),
    placeholderData: keepPreviousData,
    retry: (failures, error: Error) => retryOutcomes(failures, error),
  });
}

export function usePerformance(): UseQueryResult<Performance> {
  return useQuery({
    queryKey: ['performance'],
    queryFn: ({ signal }) => request<Performance>('/performance', { signal }),
  });
}

/**
 * Score an uploaded file. A plain request, not a hook: the file and its rows
 * are never cached, and a submission is retried only when the reader asks.
 */
export function scoreBatch(file: File, signal: AbortSignal): Promise<BatchScore> {
  const form = new FormData();
  form.append('file', file, file.name);
  return request<BatchScore>('/batch/score', { method: 'POST', form, signal });
}
