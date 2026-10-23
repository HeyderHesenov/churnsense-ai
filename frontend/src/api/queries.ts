/**
 * One hook per endpoint. The book does not change while it is served, so
 * what is fetched once stays fresh for the session; a filter or an assumption
 * is part of the key, and a superseded request is aborted (React Query passes
 * the AbortSignal through to fetch).
 */
import {
  keepPreviousData,
  QueryClient,
  useMutation,
  useQuery,
  type UseQueryResult,
} from '@tanstack/react-query';
import { ApiError, request, type Schemas } from './client';

export type ModelInfo = Schemas['ModelInfoResponse'];
export type Overview = Schemas['OverviewResponse'];
export type CustomerPage = Schemas['CustomerPage'];
export type CustomerRow = Schemas['CustomerRow'];
export type CustomerDetail = Schemas['CustomerDetail'];
export type CustomerFeatures = Schemas['CustomerFeatures'];
export type Examples = Schemas['ExamplesResponse'];
export type ExplainResponse = Schemas['ExplainResponse'];
export type Explanation = Schemas['Explanation'];
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
export type Partition = 'train' | 'validation' | 'test' | 'unknown';

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
    ids: query.ids,
    sort: query.sort,
    dir: query.dir,
    page: query.page,
    page_size: query.page_size,
  };
}

/**
 * An empty band list chooses no band. The API reads a missing ``bands`` as
 * "any band", so that choice is never sent: the query stays idle and the page
 * says nothing is chosen.
 */
function choosesSomething(filter: CustomerFilter): boolean {
  return filter.bands?.length !== 0;
}

export function useCustomers(query: CustomerQuery): UseQueryResult<CustomerPage> {
  return useQuery({
    queryKey: ['customers', query],
    queryFn: ({ signal }) =>
      request<CustomerPage>('/customers', { query: customerParams(query), signal }),
    placeholderData: keepPreviousData,
    enabled: choosesSomething(query),
  });
}

/** How many customers a filter keeps, for a preset's count. */
export function useCustomerCount(filter: CustomerFilter): UseQueryResult<number> {
  return useQuery({
    queryKey: ['customers', 'count', filter],
    queryFn: async ({ signal }) =>
      (
        await request<CustomerPage>('/customers', {
          query: customerParams({ ...filter, page_size: 1 }),
          signal,
        })
      ).total,
    enabled: choosesSomething(filter),
  });
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
  });
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

export function useSegments(rows: string, cols: string | null): UseQueryResult<Segments> {
  return useQuery({
    queryKey: ['segments', rows, cols],
    queryFn: ({ signal }) => request<Segments>('/segments', { query: { rows, cols }, signal }),
    placeholderData: keepPreviousData,
  });
}

export function useGlobalExplanation(): UseQueryResult<GlobalExplanation> {
  return useQuery({
    queryKey: ['explain', 'global'],
    queryFn: ({ signal }) => request<GlobalExplanation>('/explain/global', { signal }),
  });
}

export interface Assumptions {
  cost: number;
  success: number;
  horizon: number;
  margin: number;
}

export function useSweep(assumptions: Assumptions | null): UseQueryResult<Sweep> {
  return useQuery({
    queryKey: ['sweep', assumptions],
    queryFn: ({ signal }) =>
      request<Sweep>('/threshold/sweep', { query: { ...assumptions }, signal }),
    placeholderData: keepPreviousData,
  });
}

export function usePerformance(): UseQueryResult<Performance> {
  return useQuery({
    queryKey: ['performance'],
    queryFn: ({ signal }) => request<Performance>('/performance', { signal }),
  });
}

/** Score an uploaded file; never cached, never retried. */
export function useBatchScore() {
  return useMutation({
    mutationFn: (file: File) => {
      const form = new FormData();
      form.append('file', file, file.name);
      return request<BatchScore>('/batch/score', { method: 'POST', form });
    },
    retry: false,
  });
}
