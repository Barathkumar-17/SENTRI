import { useEffect, useState, type ReactNode } from 'react'
import { AlertTriangle, CheckCircle2, CircleCheck, CircleDashed, CircleHelp, Info, Loader2, OctagonX, RefreshCw, WifiOff, XCircle } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import type { ApiError, Breakdown, Run } from './api'

// ---- hash routing: no server route per page --------------------------------------------------------------
export function useRoute(): string[] {
  const read = () => (window.location.hash.replace(/^#\/?/, '') || 'home').split('/').filter(Boolean)
  const [parts, setParts] = useState(read)
  useEffect(() => {
    const on = () => setParts(read())
    window.addEventListener('hashchange', on)
    return () => window.removeEventListener('hashchange', on)
  }, [])
  return parts
}
export const go = (path: string) => {
  window.location.hash = path
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

// ---- status vocabulary: every state has an icon and words, never colour alone -----------------------------
type Tone = 'ok' | 'bad' | 'warn' | 'info' | 'outline' | ''
const ICONS = { ok: CheckCircle2, bad: XCircle, warn: AlertTriangle, info: Info, outline: CircleDashed, '': CircleHelp }

/** "1 step", "3 steps"; nothing when the count was never recorded. */
export function count(n: number | null | undefined, noun: string, plural = `${noun}s`): string {
  return n == null ? '' : `${num(n)} ${n === 1 ? noun : plural}`
}

export function Badge({ tone = '', children, title, icon }: { tone?: Tone; children: ReactNode; title?: string; icon?: LucideIcon }) {
  const Icon = icon ?? ICONS[tone]
  return (
    <span className={`badge ${tone}`} title={title}>
      <Icon size={13} aria-hidden="true" />
      {children}
    </span>
  )
}

export const VERDICT: Record<string, [Tone, string]> = {
  fail: ['bad', 'Confirmed'],
  flaky: ['warn', 'Flaky'],
  unconfirmed: ['outline', 'Not reproduced'],
  pass: ['ok', 'Fixed'],
}
export const ORIGIN: Record<string, string> = {
  ai: 'Model-chosen action',
  ai_suspicion: 'Model suspicion',
  code_probe: 'Sentri probe',
  code_fallback: 'Code fallback step',
}
export const BUG_STATUS: Record<string, [Tone, string]> = {
  open: ['bad', 'Open'],
  flaky: ['warn', 'Flaky'],
  fixed: ['ok', 'Fixed'],
  intended: ['outline', 'Intended'],
}
export const CHECK: Record<string, [Tone, string]> = {
  pass: ['ok', 'Pass'],
  fail: ['bad', 'Fail'],
  flaky: ['warn', 'Flaky'],
  na: ['outline', 'N/A'],
  error: ['warn', 'Inconclusive'],
}

export function RunStatus({ run }: { run: Run }) {
  if (run.status === 'running') return <Badge tone="info">Running</Badge>
  if (run.status === 'aborted') return <Badge tone="bad">Aborted</Badge>
  return (
    <Badge tone="outline" icon={CircleCheck}>
      Finished
    </Badge>
  )
}

/** Readiness exactly as the backend decided it; insufficient coverage is never shown as a number. */
export function Readiness({ breakdown }: { breakdown: Breakdown | null }) {
  if (!breakdown) return <span className="faint">–</span>
  if (breakdown.status === 'insufficient_coverage' || breakdown.score == null) return <Badge tone="warn">Insufficient coverage</Badge>
  return (
    <span className="score">
      <span className="mono">{breakdown.score}</span>
      <span className="of"> / 100</span>
    </span>
  )
}

/** What the readiness number was built from, in words, straight from the recorded breakdown. */
export function readinessBasis(b: Breakdown | null): string {
  if (!b) return ''
  if (b.status === 'insufficient_coverage' || b.score == null)
    return `no applicable baseline check ran${b.na ? ` (${b.na} not applicable)` : ''}`
  const parts = [`${b.passed} of ${count(b.applicable, 'applicable check')} passed`]
  if (b.flaky) parts.push(`${b.flaky} flaky (half credit)`)
  if (b.regressed) parts.push(`−${(b.penalty_per_regression ?? 5) * b.regressed} for ${count(b.regressed, 'regression')}`)
  if (b.agentic_impact) parts.push(`−${b.agentic_impact} for confirmed findings`)
  return parts.join(', ')
}

export const READINESS_NOTE =
  'Readiness is the share of applicable baseline checks that passed (flaky counts half) on a 0–100 scale, minus 5 per regression and points for each finding confirmed by replay. It counts only what this run tested and is not a safety rating.'

export function quotaStopped(run: Run): string | null {
  const reason = run.usage?.stop_reason || run.coverage?.completion?.model_stop || run.coverage?.agentic?.exploration_stop || ''
  return /quota|budget|limit/i.test(reason) ? reason : null
}

// ---- page frame -------------------------------------------------------------------------------------------
export function PageHead({ title, context, crumb, action }: { title: ReactNode; context?: ReactNode; crumb?: ReactNode; action?: ReactNode }) {
  return (
    <header className="page-head">
      <div className="page-title">
        {crumb && <div className="crumb">{crumb}</div>}
        <h1>{title}</h1>
        {context && <div className="page-context">{context}</div>}
      </div>
      {action && <div className="row">{action}</div>}
    </header>
  )
}

export function Panel({ title, hint, action, children, flush }: { title?: ReactNode; hint?: ReactNode; action?: ReactNode; children: ReactNode; flush?: boolean }) {
  return (
    <section className="panel">
      {(title || action) && (
        <div className="panel-head">
          <div className="row">
            {title && <h2>{title}</h2>}
            {hint && <span className="hint">{hint}</span>}
          </div>
          {action}
        </div>
      )}
      <div className={`panel-body${flush ? ' flush' : ''}`}>{children}</div>
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

export function ErrorState({ error, onRetry }: { error: ApiError; onRetry?: () => void }) {
  const offline = error.status === 0
  const Icon = offline ? WifiOff : OctagonX
  return (
    <div className="state" role="alert">
      <div className="row">
        <Icon size={18} aria-hidden="true" color="var(--bad)" />
        <h3>{offline ? 'Disconnected from the Sentri server' : error.status === 404 ? 'Not found' : 'Something went wrong'}</h3>
      </div>
      <p className="wrap-anywhere">{error.message}</p>
      {onRetry && (
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

export function Tabs<T extends string>({ tabs, value, onChange }: { tabs: { id: T; label: string; count?: number }[]; value: T; onChange: (t: T) => void }) {
  return (
    <div className="tabs" role="tablist">
      {tabs.map((t) => (
        <button key={t.id} role="tab" className="tab" aria-selected={value === t.id} onClick={() => onChange(t.id)}>
          {t.label}
          {t.count != null && <span className="count">{t.count}</span>}
        </button>
      ))}
    </div>
  )
}
