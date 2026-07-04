// Typed client for the entity-matching backend. All calls go through the Vite proxy
// (see vite.config.ts) so paths are same-origin.

export interface ColumnInfo {
  name: string
  sample: string[]
}

export interface UploadResponse {
  file_id: string
  filename: string
  rows: number
  columns: ColumnInfo[]
}

export interface FieldSpec {
  field: string
  label: string
  required: boolean
  help: string
}

export interface SessionConfig {
  use_deterministic: boolean
  use_probabilistic: boolean
  use_fuzzy: boolean
  thresholds: { match: number; review: number }
  fuzzy_threshold: number
  probabilistic_threshold: number
}

export interface StatusResponse {
  session_id: string
  state: 'pending' | 'running' | 'done' | 'error'
  stage: string
  progress: number
  error: string | null
  counts: Record<string, number>
}

export interface MatchPair {
  idx_a: number
  idx_b: number
  score: number
  stage: string
  rule: string
  match_class: string
  record_a: Record<string, string>
  record_b: Record<string, string>
}

export interface ResultsResponse {
  session_id: string
  total: number
  page: number
  page_size: number
  counts: Record<string, number>
  pairs: MatchPair[]
}

export type Mode = 'link' | 'dedupe'

export interface ClusterMember {
  row_idx: number
  record: Record<string, string>
}

export interface Cluster {
  master_id: number
  size: number
  members: ClusterMember[]
}

export interface ClustersResponse {
  session_id: string
  total: number
  page: number
  page_size: number
  counts: Record<string, number>
  clusters: Cluster[]
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

export function downloadUrl(sessionId: string): string {
  return `/api/session/${sessionId}/download`
}
