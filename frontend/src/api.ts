// Typed client for the entity-matching backend. All calls go through the Vite proxy
// (see vite.config.ts) so paths are same-origin.
//
// Core models are ALIASED from src/api-schema.d.ts, which is generated from the
// backend's OpenAPI schema (`npm run gen:api`) — so a backend model change shows up
// here as a compile error instead of silent drift. Container types whose generated
// typing is too loose (e.g. StatusResponse's freeform counts dict) stay hand-written.
import type { components } from './api-schema'

type Schemas = components['schemas']

export type ColumnInfo = Schemas['ColumnInfo']
export type UploadResponse = Schemas['UploadResponse']
export type FieldSpec = Schemas['FieldSpecOut']
export type MatchPair = Schemas['MatchPair']
export type DedupeCandidate = Schemas['DedupeCandidate']
export type Cluster = Schemas['ClusterOut']
export type ClusterMember = Schemas['ClusterMember']
export type Evaluation = Schemas['EvaluationResponse']

export interface SessionConfig {
  use_deterministic: boolean
  use_probabilistic: boolean
  use_fuzzy: boolean
  thresholds: { match: number; review: number }
  fuzzy_threshold: number
  probabilistic_threshold: number
  use_phone: boolean
  default_phone_region: string
  use_phonetic_blocking: boolean
  splink_tf_adjustments: boolean
  deterministic_corroboration: boolean
  deterministic_name_floor: number
  deterministic_demoted_score: number
  stage_thresholds?: Record<string, { match?: number | null; review?: number | null }> | null
}

export type RegistrationIdType =
  'unknown' | 'duns' | 'ein' | 'vat' | 'company_number' | 'lei' | 'other'

// Summary counts; stage_timings is a nested {stage: seconds} object.
export type Counts = Record<string, number> & {
  stage_timings?: Record<string, number>
}

export interface StatusResponse {
  session_id: string
  state: 'pending' | 'running' | 'done' | 'error'
  stage: string
  progress: number
  error: string | null
  counts: Counts
}

export type Decision = 'confirmed' | 'dismissed' | 'pending'

export interface ResultsResponse {
  session_id: string
  total: number
  page: number
  page_size: number
  counts: Record<string, number>
  pairs: MatchPair[]
}

export type Mode = 'link' | 'dedupe'

export interface ClustersResponse {
  session_id: string
  total: number
  page: number
  page_size: number
  counts: Record<string, number>
  clusters: Cluster[]
}

export interface CandidatesResponse {
  session_id: string
  total: number
  page: number
  page_size: number
  counts: Record<string, number>
  candidates: DedupeCandidate[]
}

async function jsonOrThrow<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let detail = res.statusText
    try {
      detail = (await res.json()).detail ?? detail
    } catch {
      /* ignore */
    }
    throw new Error(detail)
  }
  return res.json() as Promise<T>
}

export async function getFields(): Promise<FieldSpec[]> {
  return jsonOrThrow(await fetch('/api/fields'))
}

export async function uploadFile(file: File): Promise<UploadResponse> {
  const form = new FormData()
  form.append('file', file)
  return jsonOrThrow(await fetch('/api/upload', { method: 'POST', body: form }))
}

export async function createSession(body: {
  mode: Mode
  file_id_a: string
  mapping_a: Record<string, string>
  file_id_b?: string | null
  mapping_b?: Record<string, string>
  registration_id_type_a?: RegistrationIdType
  registration_id_type_b?: RegistrationIdType
  config: SessionConfig
}): Promise<{ session_id: string }> {
  return jsonOrThrow(
    await fetch('/api/session', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }),
  )
}

export async function runSession(sessionId: string): Promise<StatusResponse> {
  return jsonOrThrow(await fetch(`/api/session/${sessionId}/run`, { method: 'POST' }))
}

export async function getStatus(sessionId: string): Promise<StatusResponse> {
  return jsonOrThrow(await fetch(`/api/session/${sessionId}/status`))
}

export async function getResults(
  sessionId: string,
  opts: { matchClass?: string; page?: number; pageSize?: number } = {},
): Promise<ResultsResponse> {
  const p = new URLSearchParams()
  if (opts.matchClass) p.set('match_class', opts.matchClass)
  p.set('page', String(opts.page ?? 0))
  p.set('page_size', String(opts.pageSize ?? 50))
  return jsonOrThrow(await fetch(`/api/session/${sessionId}/results?${p}`))
}

export async function getClusters(
  sessionId: string,
  opts: { filter?: string; page?: number; pageSize?: number } = {},
): Promise<ClustersResponse> {
  const p = new URLSearchParams()
  if (opts.filter) p.set('filter', opts.filter)
  p.set('page', String(opts.page ?? 0))
  p.set('page_size', String(opts.pageSize ?? 25))
  return jsonOrThrow(await fetch(`/api/session/${sessionId}/clusters?${p}`))
}

export async function getEvaluation(sessionId: string): Promise<Evaluation> {
  return jsonOrThrow(await fetch(`/api/session/${sessionId}/evaluation`))
}

export async function uploadGroundTruth(sessionId: string, file: File): Promise<Evaluation> {
  const form = new FormData()
  form.append('file', file)
  return jsonOrThrow(
    await fetch(`/api/session/${sessionId}/ground_truth`, {
      method: 'POST',
      body: form,
    }),
  )
}

export async function reclassify(
  sessionId: string,
  match: number,
  review: number,
): Promise<{ session_id: string; match: number; review: number; counts: Counts }> {
  return jsonOrThrow(
    await fetch(`/api/session/${sessionId}/reclassify`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ match, review }),
    }),
  )
}

export async function postBulkDecisions(
  sessionId: string,
  pairs: { idx_a: number; idx_b: number }[],
  decision: Decision,
): Promise<{ session_id: string; counts: Counts }> {
  return jsonOrThrow(
    await fetch(`/api/session/${sessionId}/decisions/bulk`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ pairs: pairs.map((p) => ({ ...p, decision })) }),
    }),
  )
}

export async function postDecision(
  sessionId: string,
  idxA: number,
  idxB: number,
  decision: Decision,
): Promise<{ session_id: string; counts: Record<string, number> }> {
  return jsonOrThrow(
    await fetch(`/api/session/${sessionId}/decision`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ idx_a: idxA, idx_b: idxB, decision }),
    }),
  )
}

export async function getCandidates(
  sessionId: string,
  opts: { filter?: string; page?: number; pageSize?: number } = {},
): Promise<CandidatesResponse> {
  const p = new URLSearchParams()
  if (opts.filter) p.set('filter', opts.filter)
  p.set('page', String(opts.page ?? 0))
  p.set('page_size', String(opts.pageSize ?? 25))
  return jsonOrThrow(await fetch(`/api/session/${sessionId}/candidates?${p}`))
}

export function downloadUrl(sessionId: string): string {
  return `/api/session/${sessionId}/download`
}
