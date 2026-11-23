// Typed client for the local Sentri server. Only real endpoints (src/sentri/dashboard/*.py); no sample data.
import { useCallback, useEffect, useRef, useState } from 'react'

export type RoleUsage = {
  calls: number
  ok: number
  failed: number
  retries: number
  input_tokens: number
  output_tokens: number
  cost_usd: number
  unpriced_calls: number
  seconds: number
}

export type Usage = {
  provider?: string
  calls: number
  failed_calls?: number
  refused_calls?: number
  retries: number
  input_tokens: number
  output_tokens: number
  cost_usd: number | null
  unpriced_calls?: number
  stop_reason: string | null
  limits?: Record<string, unknown>
  roles?: Record<string, RoleUsage>
}

export type AgenticCoverage = {
  pages?: number
  endpoints?: number
  hypotheses?: number
  start_as?: string | null
  steps?: number
  ai_steps?: number
  code_probe_steps?: number
  fallback_steps?: number
  invalid_steps?: number
  distinct_states?: number
  transitions?: number
  known_dead_end_refusals?: number
  goals_done?: number
  goals_added?: number
  candidates?: number
  confirmed?: number
  unconfirmed?: number
  inconclusive?: number
  exploration_stop?: string
  untried_options_left?: number
  states_by_identity?: Record<string, number>
  notes?: string[]
}

export type Coverage = {
  baseline?: { flows?: string[]; checks?: Record<string, number>; probes?: number }
  agentic?: AgenticCoverage | null
  completion?: { phases?: Record<string, string>; model_stop?: string | null }
  replay_state?: string
  caveat?: string
}

export type Breakdown = {
  score: number | null
  status?: 'scored' | 'insufficient_coverage'
  passed: number
  flaky: number
  failed: number
  applicable: number
  na: number
  errors: number
  regressed: number
  penalty_per_regression?: number
  formula: string
  baseline?: number | null
  agentic_impact?: number
  agentic_counts?: Record<string, number>
}

export type Run = {
  id: number
  commit_sha: string | null
  branch: string | null
  started_at: string | null
  finished_at: string | null
  status: 'running' | 'finished' | 'aborted'
  readiness: number | null
  duration_s: number | null
  llm_cost_usd: number | null
  llm_mode: string | null
  breakdown: Breakdown | null
  usage: Usage | null
  coverage: Coverage | null
}

export type ReproStep = { action: Record<string, unknown>; match?: Record<string, string> | null; expect_ok?: boolean; setup?: boolean }

export type Finding = {
  id: number
  run_id: number
  hypothesis_id: string | null
  title: string | null
  oracle_ids: string | null
  repro: ReproStep[]
  from_step: number | null
  to_step: number | null
  verdict: 'fail' | 'flaky' | 'unconfirmed' | 'pass'
  attempts: number
  check_id: number | null
  origin: string | null
  replay_state: string | null
  evidence: string[]
  notes: string[]
  commit_sha?: string | null
  branch?: string | null
  key?: string | null
  created_at?: string
}

export type Bug = {
  id: string
  flow_id: string
  attack_id: string
  oracle_id: string
  status: 'open' | 'fixed' | 'flaky' | 'intended'
  first_commit: string | null
  fixed_commit: string | null
  last_seen_commit: string | null
  regressions: number
  human_note: string | null
  needs_review: number
  sample_detail: string | null
}

export type Attempt = {
  attempt: number
  missing?: boolean
  completed?: boolean
  notes?: string[]
  oracles?: { oracle_id: string; detail: string; route?: string | null }[]
  responses?: { identity: string; method: string; path: string; status: number | null; text?: string }[]
  probes?: Record<string, unknown>[]
  health?: Record<string, unknown>[]
  server_log_tail?: string[]
}

export type FindingDetail = Finding & { bugs: Bug[]; attempts: Attempt[] }

export type BugHistory = { id: number; bug_id: string; ts: string; from_status: string | null; to_status: string; commit_sha: string | null; run_id: number | null; actor: 'system' | 'human'; note: string | null }

export type Step = {
  step: number
  parent_step: number | null
  hypothesis_id: string | null
  identity: string | null
  thought: string | null
  action_text: string | null
  ok: number | boolean
  error: string | null
  url_after: string | null
  signals: { oracle_id: string; detail: string }[]
  origin: string | null
}

export type Agentic = {
  app_map: { pages?: { path: string; requires_login?: boolean }[]; endpoints?: unknown[] } | null
  strategy: { strategy?: { app_kind?: string; hypotheses?: { id: string; title: string }[] } } | null
  findings: Finding[]
  steps: Step[]
}

export type GridCell = {
  check_id: number
  flow_id: string
  attack_id: string
  verdict: string
  score_verdict: string
  oracle_hits: string
  bug_ids: string[]
  reused: boolean
}
export type Grid = { flows: string[]; attacks: string[]; cells: GridCell[] }

export type SentriEvent = { id: number; run_id: number | null; ts: string; kind: string; message: string; reason: string | null; data: Record<string, unknown> }

export type Job = { id: string; kind: string; status: string; created_at: string; finished_at: string | null; run_id: number | null; result: Record<string, unknown> | null; error: string | null }

export type Provider = { id: string; label: string; kind: string; base_url: string | null; needs_key: boolean; local: boolean; key_env: string | null; note: string }
export type ModelChoice = { preset: string; base_url: string | null; model: string | null; model_smart?: string | null; has_key?: boolean; api_key?: string | null }

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response
  try {
    res = await fetch(path, { ...init, headers: { ...(init?.body ? { 'Content-Type': 'application/json' } : {}), ...init?.headers } })
  } catch {
    throw new ApiError(0, 'Cannot reach the Sentri server. Is `sentri ui` still running?')
  }
  if (!res.ok) {
    let detail = res.statusText
    try {
      const body = await res.json()
      detail = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail ?? body)
    } catch {
      detail = (await res.text().catch(() => '')) || detail
    }
    throw new ApiError(res.status, detail)
  }
  if (res.status === 204) return undefined as T
  return (await res.json()) as T
}

export const api = {
  runs: () => request<Run[]>('/api/runs?limit=100'),
  run: (id: number) => request<Run>(`/api/runs/${id}`),
  grid: (id: number) => request<Grid>(`/api/runs/${id}/grid`),
  agentic: (id: number) => request<Agentic>(`/api/runs/${id}/agentic`),
  events: (runId: number) => request<SentriEvent[]>(`/api/events?run_id=${runId}&limit=2000`),
  findings: (verdict?: string) => request<Finding[]>(`/api/findings${verdict ? `?verdict=${verdict}` : ''}`),
  finding: (id: number) => request<FindingDetail>(`/api/findings/${id}`),
  bugHistory: (id: string) => request<BugHistory[]>(`/api/bugs/${encodeURIComponent(id)}/history`),
  bugs: (status?: string) => request<Bug[]>(`/api/bugs${status ? `?status=${status}` : ''}`),
  setBugStatus: (id: string, status: 'intended' | 'open', note: string, by: string) =>
    request<Bug>(`/api/bugs/${id}/status`, { method: 'POST', body: JSON.stringify({ status, note, by }) }),
  providers: () => request<Provider[]>('/api/providers'),
  getModel: () => request<ModelChoice>('/api/settings/model'),
  putModel: (choice: ModelChoice) => request<ModelChoice>('/api/settings/model', { method: 'PUT', body: JSON.stringify(choice) }),
  testModel: (choice: ModelChoice) => request<{ ok: boolean; detail?: string; error?: string }>('/api/models/test', { method: 'POST', body: JSON.stringify(choice) }),
  inspect: (path: string) => request<{ path: string; name: string; contracts: string[]; suggestions: { kind: string; run?: string; port?: number; source?: string; framework?: string }[]; notes: string[] }>('/api/projects/inspect', { method: 'POST', body: JSON.stringify({ path }) }),
  validate: (yaml: string) => request<{ valid: boolean; errors: string[]; approved: boolean; flows: string[] }>('/api/contracts/validate', { method: 'POST', body: JSON.stringify({ yaml }) }),
  saveContract: (path: string, yaml: string, approve_as: string | null) =>
    request<Record<string, unknown>>('/api/contracts/save', { method: 'POST', body: JSON.stringify({ path, yaml, approve_as }) }),
  plan: (body: Record<string, unknown>) => request<Job>('/api/plan', { method: 'POST', body: JSON.stringify(body) }),
  startRun: (body: Record<string, unknown>) => request<Job>('/api/runs', { method: 'POST', body: JSON.stringify(body) }),
  jobs: () => request<Job[]>('/api/jobs'),
  job: (id: string) => request<Job>(`/api/jobs/${id}`),
  cancelJob: (id: string) => request<Job>(`/api/jobs/${id}/cancel`, { method: 'POST' }),
}

export type Loadable<T> = { data: T | null; error: ApiError | null; loading: boolean; reload: () => void }

/** Fetch on mount and on `deps` change; `pollMs` keeps it fresh (e.g. while a run is running). */
export function useApi<T>(load: () => Promise<T>, deps: unknown[], pollMs?: number): Loadable<T> {
  // Results are tied to the request that produced them, so a page reused for another id (run 2 -> run 77)
  // never shows the previous item's data next to the new one's error.
  const key = JSON.stringify(deps)
  const [result, setResult] = useState<{ key: string; data: T | null; error: ApiError | null; done: boolean }>({ key, data: null, error: null, done: false })
  const [tick, setTick] = useState(0)
  const reload = useCallback(() => setTick((t) => t + 1), [])
  useEffect(() => {
    let alive = true
    load()
      .then((d) => alive && setResult({ key, data: d, error: null, done: true }))
      .catch((e: ApiError) => alive && setResult((r) => ({ key, data: r.key === key ? r.data : null, error: e, done: true })))
    return () => {
      alive = false
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, tick])
  useEffect(() => {
    if (!pollMs) return
    const t = setInterval(reload, pollMs)
    return () => clearInterval(t)
  }, [pollMs, reload])
  const current = result.key === key
  return { data: current ? result.data : null, error: current ? result.error : null, loading: !current || !result.done, reload }
}

export type StreamState = 'connecting' | 'open' | 'disconnected'

/** Live events of one run over Server-Sent Events, with the connection state for a "disconnected" banner. */
export function useEventStream(runId: number, enabled: boolean, onEvent: (e: SentriEvent) => void): StreamState {
  const [state, setState] = useState<StreamState>('connecting')
  const handler = useRef(onEvent)
  useEffect(() => {
    handler.current = onEvent
  })
  useEffect(() => {
    if (!enabled) return
    const source = new EventSource(`/api/events/stream?run_id=${runId}`)
    source.onopen = () => setState('open')
    source.onerror = () => setState(source.readyState === EventSource.CLOSED ? 'disconnected' : 'connecting')
    const listener = (msg: MessageEvent) => {
      try {
        const body = JSON.parse(msg.data)
        handler.current({ id: Number(msg.lastEventId), kind: msg.type, ...body })
      } catch {
        /* ignore malformed frames */
      }
    }
    // The server names each event by its kind; subscribe generically via onmessage-like wildcard.
    const kinds = KNOWN_EVENT_KINDS
    kinds.forEach((k) => source.addEventListener(k, listener))
    return () => {
      kinds.forEach((k) => source.removeEventListener(k, listener))
      source.close()
    }
  }, [runId, enabled])
  return enabled ? state : 'disconnected'
}

export const KNOWN_EVENT_KINDS = [
  'run_started', 'sandbox_starting', 'sandbox_ready', 'preflight', 'phase', 'recon', 'setup', 'strategy', 'recheck',
  'explore_step', 'candidate', 'finding', 'focus', 'llm_fallback', 'agentic_finished', 'agentic_skipped',
  'check_started', 'action', 'attempt_finished', 'check_finished', 'check_reused', 'probe_queued',
  'bug_opened', 'bug_fixed', 'regression', 'bug_needs_review', 'timing', 'llm_usage', 'warning',
  'replay_state', 'run_finished', 'run_aborted',
]
