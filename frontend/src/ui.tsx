import { useEffect, useState, type ReactNode } from 'react'
import { AlertTriangle, CheckCircle2, CircleCheck, CircleDashed, CircleHelp, Info, Loader2, OctagonX, RefreshCw, WifiOff, XCircle } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import type { ApiError, Breakdown, Run } from './api'

// ---- hash routing: #/path/parts?filter=value. Filters live in the URL so reload, back and links keep them. ----
export type Route = { parts: string[]; query: URLSearchParams }

function readRoute(): Route {
  const raw = window.location.hash.replace(/^#\/?/, '')
  const [path, qs = ''] = raw.split('?')
  return { parts: (path || 'home').split('/').filter(Boolean), query: new URLSearchParams(qs) }
}

export function useRoute(): Route {
  const [route, setRoute] = useState(readRoute)
  useEffect(() => {
    const on = () => setRoute(readRoute())
    window.addEventListener('hashchange', on)
    return () => window.removeEventListener('hashchange', on)
  }, [])
  return route
}

/** "#/findings?verdict=fail" from a path and the non-empty query values. */
export function href(path: string, query?: Record<string, string | number | null | undefined>): string {
  const q = new URLSearchParams()
  for (const [k, v] of Object.entries(query ?? {})) if (v != null && v !== '') q.set(k, String(v))
  const s = q.toString()
  return `#/${path.replace(/^\//, '')}${s ? `?${s}` : ''}`
}

export const go = (path: string) => {
  window.location.hash = path.startsWith('#') ? path.slice(1) : path
}

// The findings list a person came from, so a detail page's breadcrumb returns to the same filters.
const LIST_KEY = 'sentri.findings.list'
export function rememberList(hash: string) {
  try {
    sessionStorage.setItem(LIST_KEY, hash)
  } catch {
    /* storage may be unavailable; the plain list is the fallback */
  }
}
export function listBack(fallback = '#/findings'): string {
  try {
    return sessionStorage.getItem(LIST_KEY) || fallback
  } catch {
    return fallback
  }
}

// ---- formatting --------------------------------------------------------------------------------------------
export const short = (sha?: string | null) => (sha && sha !== 'local' ? sha.slice(0, 7) : 'local')
export const num = (n?: number | null) => (n == null ? '–' : n.toLocaleString('en-US'))
export function duration(s?: number | null): string {
  if (s == null) return '–'
  const m = Math.floor(s / 60)
  const sec = Math.round(s % 60)
  return m ? `${m}m ${String(sec).padStart(2, '0')}s` : `${sec}s`
}
export function when(iso?: string | null): string {
  if (!iso) return '–'
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })
}
export const cost = (usd?: number | null, calls?: number) =>
  calls === 0 ? 'no model calls' : usd == null ? 'cost unknown' : `$${usd.toFixed(usd < 0.01 ? 4 : 2)}`

/** "1 step", "3 steps"; nothing when the count was never recorded. */
export function count(n: number | null | undefined, noun: string, plural = `${noun}s`): string {
  return n == null ? '' : `${num(n)} ${n === 1 ? noun : plural}`
}

// ---- status vocabulary: every state has an icon and words, never colour alone -----------------------------
export type Tone = 'ok' | 'bad' | 'warn' | 'info' | ''
const ICONS: Record<Tone, LucideIcon> = { ok: CheckCircle2, bad: XCircle, warn: AlertTriangle, info: Info, '': CircleHelp }

export function Badge({ tone = '', children, title, icon }: { tone?: Tone; children: ReactNode; title?: string; icon?: LucideIcon }) {
  const Icon = icon ?? ICONS[tone]
  return (
    <span className={`badge ${tone}`} title={title}>
      <Icon size={13} aria-hidden="true" />
      {children}
    </span>
  )
}

export const VERDICT: Record<string, [Tone, string, LucideIcon?]> = {
  fail: ['bad', 'Confirmed'],
  flaky: ['warn', 'Flaky'],
  unconfirmed: ['', 'Not reproduced', CircleDashed],
  pass: ['ok', 'Fixed'],
}
export function VerdictBadge({ verdict }: { verdict: string }) {
  const [tone, label, icon] = VERDICT[verdict] ?? ['', verdict]
  return <Badge tone={tone} icon={icon}>{label}</Badge>
}
export const ORIGIN: Record<string, string> = {
  ai: 'Model-chosen action',
  ai_suspicion: 'Model suspicion',
  code_probe: 'Sentri probe',
  code_fallback: 'Code fallback step',
  harness: 'Test harness (fixture)',
}
export const BUG_STATUS: Record<string, [Tone, string, LucideIcon?]> = {
  open: ['bad', 'Open'],
  flaky: ['warn', 'Flaky'],
  fixed: ['ok', 'Fixed'],
  intended: ['', 'Intended', CircleDashed],
}
export function BugBadge({ status }: { status: string }) {
  const [tone, label, icon] = BUG_STATUS[status] ?? ['', status]
  return <Badge tone={tone} icon={icon}>{label}</Badge>
}
export const CHECK: Record<string, [Tone, string, LucideIcon?]> = {
  pass: ['ok', 'Pass'],
  fail: ['bad', 'Fail'],
  flaky: ['warn', 'Flaky'],
  na: ['', 'N/A', CircleDashed],
  error: ['warn', 'Inconclusive'],
}

export function RunStatus({ run }: { run: Run }) {
  if (run.status === 'running') return <Badge tone="info" icon={Loader2}>Running</Badge>
  if (run.status === 'aborted') return <Badge tone="bad">Aborted</Badge>
  return <Badge icon={CircleCheck}>Finished</Badge>
}

export const insufficient = (b: Breakdown | null | undefined) => !b || b.status === 'insufficient_coverage' || b.score == null

/** Readiness exactly as the backend decided it; insufficient coverage is never shown as a number. */
export function Readiness({ breakdown }: { breakdown: Breakdown | null }) {
  if (!breakdown) return <span className="faint">not recorded</span>
  if (insufficient(breakdown)) return <Badge tone="warn">Insufficient coverage</Badge>
  return (
    <span className="score">
      <span className="n">{breakdown.score}</span>
      <span className="of"> / 100</span>
    </span>
  )
}

/** What the readiness number was built from, in words, straight from the recorded breakdown. */
export function readinessBasis(b: Breakdown | null): string {
  if (!b) return ''
  if (insufficient(b)) return `no applicable baseline check ran${b.na ? ` (${b.na} not applicable)` : ''}`
  const parts = [`${b.passed} of ${count(b.applicable, 'applicable check')} passed`]
  if (b.flaky) parts.push(`${b.flaky} flaky (half credit)`)
  if (b.regressed) parts.push(`−${(b.penalty_per_regression ?? 5) * b.regressed} for ${count(b.regressed, 'regression')}`)
  if (b.agentic_impact) parts.push(`−${b.agentic_impact} for confirmed findings`)
  return parts.join(', ')
}

export const READINESS_NOTE =
  'Readiness is the share of applicable baseline checks that passed (flaky counts half) on a 0–100 scale, minus 5 per regression and points for each finding confirmed by replay. It counts only what this run tested and is not a safety rating.'

/** One plain sentence for how a run came out: confirmed findings and failed baseline checks, never a pass for untested ground. */
export function outcomeLine(run: Run, confirmedFindings?: number, failedChecks?: number, flakyChecks?: number): string {
  if (run.status === 'running') return 'Still running; what is shown so far is incomplete.'
  if (run.status === 'aborted') return 'The run was aborted before it finished; only what ran before is recorded.'
  const confirmed = confirmedFindings ?? run.coverage?.agentic?.confirmed ?? 0
  const checks = run.coverage?.baseline?.checks
  // Failed and flaky are counted apart, as in report.md: a flaky check is not a failed one (it earns half credit).
  const failed = failedChecks ?? checks?.failed ?? 0
  const flaky = flakyChecks ?? checks?.flaky ?? 0
  const baseline = failed && flaky ? `${count(failed, 'baseline check')} failed, ${flaky} flaky`
    : failed ? `${count(failed, 'baseline check')} failed`
    : flaky ? `${count(flaky, 'baseline check')} ${flaky === 1 ? 'was' : 'were'} flaky` : ''
  const parts = [confirmed ? `${count(confirmed, 'finding')} confirmed by replay` : '', baseline].filter(Boolean)
  if (parts.length) return `${parts.join(' and ')}${insufficient(run.breakdown) ? '; too little applicable was tested to score it' : ''}.`
  if (insufficient(run.breakdown)) return 'Too little was tested to score this run, and nothing broke in what was tried.'
  return 'Nothing broke in what was tested.'
}

/** Why model use stopped early (a quota, a budget or a limit), or null when it did not. */
export function quotaStopped(run: Run): string | null {
  const reason = run.usage?.stop_reason || run.coverage?.completion?.model_stop || run.coverage?.agentic?.exploration_stop || ''
  return /quota|budget|limit/i.test(reason) ? reason : null
}

// ---- page frame -------------------------------------------------------------------------------------------
export function PageHead({
  title,
  purpose,
  meta,
  crumbs,
  action,
}: {
  title: ReactNode
  purpose?: ReactNode
  meta?: ReactNode
  crumbs?: { label: ReactNode; href?: string }[]
  action?: ReactNode
}) {
  return (
    <header className="page-head">
      <div className="page-title">
        {crumbs && (
          <nav className="crumbs" aria-label="Breadcrumb">
            {crumbs.map((c, i) => (
              <span key={i} className="row" style={{ gap: 8 }}>
                {i > 0 && <span aria-hidden="true">/</span>}
                {c.href ? <a href={c.href}>{c.label}</a> : <span aria-current="page">{c.label}</span>}
              </span>
            ))}
          </nav>
        )}
        <h1>{title}</h1>
        {purpose && <p className="page-purpose">{purpose}</p>}
        {meta && <div className="page-meta">{meta}</div>}
      </div>
      {action && <div className="row">{action}</div>}
    </header>
  )
}

/** A card: a real group of related content with an optional heading row. */
export function Card({
  title,
  hint,
  action,
  children,
  flush,
  className = '',
  foot,
  id,
}: {
  title?: ReactNode
  hint?: ReactNode
  action?: ReactNode
  children: ReactNode
  flush?: boolean
  className?: string
  foot?: ReactNode
  id?: string
}) {
  return (
    <section className={`card ${className}`} aria-labelledby={id && title ? `${id}-title` : undefined} id={id}>
      {(title || action) && (
        <div className="card-head">
          <div className="titles">
            {title && <h2 id={id ? `${id}-title` : undefined}>{title}</h2>}
            {hint && <span className="hint">{hint}</span>}
          </div>
          {action}
        </div>
      )}
      <div className={`card-body${flush ? ' flush' : ''}`}>{children}</div>
      {foot && <div className="card-foot">{foot}</div>}
    </section>
  )
}

// ---- states ------------------------------------------------------------------------------------------------
export function Loading({ label = 'Loading' }: { label?: string }) {
  return (
    <div className="state" role="status" aria-live="polite">
      <div className="row muted small">
        <Loader2 size={16} className="spin" aria-hidden="true" /> {label}…
      </div>
      <div className="skeleton" style={{ width: '62%' }} />
      <div className="skeleton" style={{ width: '44%' }} />
    </div>
  )
}

export function ErrorState({ error, onRetry, what = 'item' }: { error: ApiError; onRetry?: () => void; what?: string }) {
  const offline = error.status === 0
  const Icon = offline ? WifiOff : OctagonX
  return (
    <div className="state" role="alert">
      <div className="row">
        <Icon size={18} aria-hidden="true" color="var(--bad)" />
        <h3>{offline ? 'Disconnected from the Sentri server' : error.status === 404 ? `This ${what} does not exist` : 'Something went wrong'}</h3>
      </div>
      <p className="wrap-anywhere">{error.status === 404 ? 'It may belong to another ledger, or the link is wrong.' : error.message}</p>
      {onRetry && error.status !== 404 && (
        <button className="btn small" onClick={onRetry}>
          <RefreshCw size={14} aria-hidden="true" /> Try again
        </button>
      )}
    </div>
  )
}

export function Empty({ title, children, action }: { title: string; children?: ReactNode; action?: ReactNode }) {
  return (
    <div className="state">
      <h3>{title}</h3>
      {children && <p>{children}</p>}
      {action}
    </div>
  )
}

export function Banner({ tone = 'info', title, children }: { tone?: 'info' | 'warn' | 'bad'; title: ReactNode; children?: ReactNode }) {
  const Icon = tone === 'bad' ? OctagonX : tone === 'warn' ? AlertTriangle : Info
  return (
    <div className={`banner ${tone}`} role={tone === 'info' ? 'status' : 'alert'}>
      <Icon size={16} aria-hidden="true" />
      <div>
        <strong>{title}</strong>
        {children && <div>{children}</div>}
      </div>
    </div>
  )
}

export function Tabs<T extends string>({ tabs, value, onChange, label }: { tabs: { id: T; label: string; count?: number }[]; value: T; onChange: (t: T) => void; label: string }) {
  return (
    <div className="tabs" role="tablist" aria-label={label}>
      {tabs.map((t) => (
        <button key={t.id} role="tab" className="tab" aria-selected={value === t.id} onClick={() => onChange(t.id)}>
          {t.label}
          {t.count != null && <span className="count">{t.count}</span>}
        </button>
      ))}
    </div>
  )
}
