import { useState } from 'react'
import { ChevronDown, ChevronRight } from 'lucide-react'
import { api, useApi, type Attempt, type FindingDetail } from '../api'
import { Badge, Banner, Card, Empty, ErrorState, Loading, ORIGIN, PageHead, VerdictBadge, count, href, listBack, short, when } from '../ui'
import { BugDecision } from './BugDecision'

// One finding on its own page: outcome first, then the replay evidence, then the technical record, then the decision.

export function FindingPage({ id }: { id: number }) {
  const f = useApi(() => api.finding(id), [id])
  const crumbs = [{ label: 'Findings', href: listBack() }, { label: `Finding ${id}` }]
  if (f.loading && !f.data) return <Shell crumbs={crumbs} id={id}><Card><Loading label="Loading finding" /></Card></Shell>
  if (f.error && !f.data) return <Shell crumbs={crumbs} id={id}><Card><ErrorState error={f.error} onRetry={f.reload} what="finding" /></Card></Shell>
  return <FindingView f={f.data as FindingDetail} crumbs={crumbs} onChanged={f.reload} />
}

function Shell({ id, crumbs, children }: { id: number; crumbs: { label: string; href?: string }[]; children: React.ReactNode }) {
  return (
    <>
      <PageHead crumbs={crumbs} title={`Finding ${id}`} />
      <div className="page-body">{children}</div>
    </>
  )
}

/** The distinct checks that fired across the recorded replays, with how many attempts each was seen in. */
function whatBroke(attempts: Attempt[]): { oracle: string; detail: string; seen: number }[] {
  const out = new Map<string, { oracle: string; detail: string; seen: number }>()
  for (const a of attempts) {
    const here = new Set<string>()
    for (const o of a.oracles ?? []) {
      const key = `${o.oracle_id}\u0000${o.detail}`
      if (here.has(key)) continue
      here.add(key)
      const row = out.get(key) ?? { oracle: o.oracle_id, detail: o.detail, seen: 0 }
      row.seen += 1
      out.set(key, row)
    }
  }
  return [...out.values()]
}

function FindingView({ f, crumbs, onChanged }: { f: FindingDetail; crumbs: { label: string; href?: string }[]; onChanged: () => void }) {
  const recorded = f.attempts.filter((a) => !a.missing)
  const hits = recorded.filter((a) => (a.oracles?.length ?? 0) > 0).length
  const broke = whatBroke(f.attempts)
  const restart = f.replay_state !== 'none'
  return (
    <>
      <PageHead
        crumbs={crumbs}
        title={f.title || 'Untitled finding'}
        meta={
          <>
            <VerdictBadge verdict={f.verdict} />
            <span>found in <a href={href(`runs/${f.run_id}/report`)}>Run {f.run_id}</a></span>
            <span className="mono">{short(f.commit_sha)}</span>
            <span>{when(f.created_at)}</span>
          </>
        }
      />
      <div className="page-body">
        <Card title="Outcome">
          <div className="stack">
            <dl className="kv">
              <dt>{broke.length ? 'What broke' : 'Checks fired'}</dt>
              <dd>
                {broke.length ? (
                  <ul className="plain" style={{ gap: 4 }}>
                    {broke.map((b) => (
                      <li key={b.oracle + b.detail}>
                        <span className="mono">{b.oracle}</span> {b.detail}
                        {b.seen !== hits && <span className="faint"> · in {b.seen} of {recorded.length} replays</span>}
                      </li>
                    ))}
                  </ul>
                ) : (
                  <span className="muted">None in the recorded replays{f.oracle_ids ? `; the candidate was flagged for ${f.oracle_ids}` : ''}.</span>
                )}
              </dd>
              <dt>Replays</dt>
              <dd>
                <span className="row" style={{ gap: 10 }}>
                  <AttemptDots attempts={f.attempts} total={f.attempts.length || 3} />
                  {recorded.length
                    ? hits ? `Break seen in ${hits} of ${recorded.length}` : `No break seen in any of ${recorded.length}`
                    : 'No replay evidence stored'}
                  {recorded.length > 0 && <span className="faint">· {restart ? 'each from a restarted app' : 'on the live state (no reset)'}</span>}
                </span>
              </dd>
              <dt>Found by</dt>
              <dd>{ORIGIN[f.origin ?? ''] ?? f.origin ?? 'not recorded'}{f.from_step ? <span className="faint"> · exploration steps {f.from_step}–{f.to_step}</span> : <span className="faint"> · a re-check of an earlier finding</span>}</dd>
            </dl>
            {f.origin === 'harness' && <Banner tone="info" title="Test fixture">This finding was recorded by Sentri's test harness (a scripted replay), not discovered by a model.</Banner>}
          </div>
        </Card>

        <Card title="Replay evidence" hint="each attempt, as recorded" flush>
          {!f.attempts.length ? (
            <Empty title="No replay evidence">Findings recorded before evidence linking have none.</Empty>
          ) : (
            f.attempts.map((a) => <AttemptView key={a.attempt} a={a} />)
          )}
        </Card>

        <Limitations f={f} />

        <Technical f={f} />

        <Decision f={f} onChanged={onChanged} />
      </div>
    </>
  )
}

function AttemptDots({ attempts, total }: { attempts: Attempt[]; total: number }) {
  return (
    <span className="attempts" role="img" aria-label={`Replay results: ${Array.from({ length: total }, (_, i) => describeAttempt(attempts[i])).join(', ')}`}>
      {Array.from({ length: total }, (_, i) => {
        const a = attempts[i]
        return <span key={i} className={`attempt-dot ${attemptState(a)}`} title={`Attempt ${i + 1}: ${describeAttempt(a)}`} aria-hidden="true">{i + 1}</span>
      })}
    </span>
  )
}
const attemptState = (a?: Attempt) => (!a || a.missing ? '' : a.completed === false ? 'failed' : (a.oracles?.length ?? 0) > 0 ? 'hit' : 'quiet')
const describeAttempt = (a?: Attempt) => {
  const s = attemptState(a)
  return s === '' ? 'no evidence' : s === 'failed' ? 'could not replay' : s === 'hit' ? 'break seen' : 'nothing seen'
}

function AttemptView({ a }: { a: Attempt }) {
  const [open, setOpen] = useState(false)
  if (a.missing) {
    return <div className="attempt"><Banner tone="warn" title={`Attempt ${a.attempt}: evidence file missing`}>It was recorded but is no longer on disk.</Banner></div>
  }
  const Icon = open ? ChevronDown : ChevronRight
  const responses = a.responses ?? []
  return (
    <div className="attempt">
      <div className="attempt-head">
        <h3>Attempt {a.attempt}</h3>
        {a.completed === false ? <Badge tone="warn">Could not replay every step</Badge> : (a.oracles?.length ?? 0) > 0 ? <Badge tone="bad">Break seen</Badge> : <Badge tone="ok">Nothing seen</Badge>}
        <button className="btn ghost small right" aria-expanded={open} aria-controls={`att-${a.attempt}`} onClick={() => setOpen(!open)}>
          <Icon size={14} aria-hidden="true" /> {open ? 'Hide' : 'Show'} {count(responses.length, 'response')}
        </button>
      </div>
      {(a.oracles?.length ?? 0) > 0 && (
        <div className="fired">
          {a.oracles!.map((o, i) => <p key={i}><span className="mono">{o.oracle_id}</span> {o.detail}</p>)}
        </div>
      )}
      {a.notes?.map((n, i) => <p key={i} className="xs faint">{n}</p>)}
      {open && (
        <div className="stack" id={`att-${a.attempt}`} style={{ gap: 8 }}>
          <pre className="code nowrap">{responses.map((r) => `${r.identity.padEnd(5)} ${r.method.padEnd(6)} ${String(r.status ?? '–').padEnd(4)} ${r.path}`).join('\n') || 'no responses recorded'}</pre>
          {(a.server_log_tail?.length ?? 0) > 0 && (
            <>
              <p className="xs faint">Server log tail, shown to you only: no verdict reads server logs (black box).</p>
              <pre className="code">{a.server_log_tail!.join('\n')}</pre>
            </>
          )}
        </div>
      )}
    </div>
  )
}

function Limitations({ f }: { f: FindingDetail }) {
  const items: string[] = []
  if (f.replay_state === 'none') items.push('Replayed on the live state: the app was not reset between attempts, so they are not independent confirmations.')
  if (f.replay_state === 'restart') items.push('Each attempt restarted the app; state kept outside its cleaned files (an external database) survives a restart.')
  if (f.origin === 'code_probe') items.push("Found by one of Sentri's own probes, not by the model's choice of action.")
  if (f.origin === 'harness') items.push('Fixed input from a validation harness: this checks the pipeline and is not an autonomous discovery.')
  if (f.origin === 'ai_suspicion') items.push('Raised by the model without a measured signal; only the replay verdict counts.')
  if (f.verdict === 'unconfirmed') items.push('Not reproduced in replay: this is not evidence that the behaviour is correct.')
  if (f.attempts.some((a) => a.completed === false)) items.push('At least one attempt could not run every step, so it neither confirms nor clears the finding.')
  if (!items.length) return null
  return (
    <Card title="Limitations" className="quiet">
      <ul className="bullets small">{items.map((t) => <li key={t}>{t}</li>)}</ul>
    </Card>
  )
}

function describe(action: Record<string, unknown>): string {
  const { kind, ...rest } = action
  const args = Object.entries(rest)
    .filter(([, v]) => v !== null && v !== undefined && v !== '' && !(Array.isArray(v) && !v.length))
    .map(([k, v]) => `${k}=${typeof v === 'string' ? JSON.stringify(v.length > 120 ? v.slice(0, 117) + '…' : v) : JSON.stringify(v)}`)
  return `${String(kind)}(${args.join(', ')})`
}
const outline = (action: Record<string, unknown>) => {
  const target = action.path ?? action.user ?? action.url ?? action.selector ?? ''
  return `${String(action.kind)}${target ? ` ${String(target)}` : ''}`
}

/** The technical record, quieter: the check key, the steps a replay re-sends, and any replay notes. */
function Technical({ f }: { f: FindingDetail }) {
  const [open, setOpen] = useState(false)
  const Icon = open ? ChevronDown : ChevronRight
  const setup = f.repro.filter((s) => s.setup).length
  return (
    <Card
      title="Technical details"
      hint={`${count(f.repro.length, 'step')} replayed${setup ? `, ${setup} of them setup` : ''}`}
      action={f.repro.length > 0 && (
        <button className="btn ghost small" aria-expanded={open} aria-controls="finding-steps" onClick={() => setOpen(!open)}>
          <Icon size={14} aria-hidden="true" /> {open ? 'Hide steps' : 'Show steps'}
        </button>
      )}
    >
      <div className="stack">
        <dl className="kv">
          {f.key && <><dt>Check</dt><dd className="mono">{f.key}</dd></>}
          {f.oracle_ids && <><dt>Flagged for</dt><dd className="mono">{f.oracle_ids}</dd></>}
          {f.hypothesis_id && <><dt>Hypothesis</dt><dd className="mono">{f.hypothesis_id}</dd></>}
        </dl>
        {!f.repro.length ? <p className="muted small">No steps stored.</p> : open ? (
          <ol className="steps" id="finding-steps">
            {f.repro.map((s, i) => (
              <li key={i} className={s.setup ? 'setup' : undefined}>
                <span className="n">{i + 1}</span>
                <div>
                  <div className="act">{describe(s.action)}</div>
                  {s.setup && <div className="faint xs">setup from an earlier line of attack</div>}
                  {s.match && <div className="faint xs mono">re-matched: {s.match.method} {s.match.path}</div>}
                </div>
              </li>
            ))}
          </ol>
        ) : (
          <p className="outline" id="finding-steps">{f.repro.map((s) => outline(s.action)).join('  →  ')}</p>
        )}
        {f.notes.length > 0 && (
          <div className="stack" style={{ gap: 6 }}>
            <h3>Replay notes</h3>
            <pre className="code">{f.notes.join('\n')}</pre>
          </div>
        )}
      </div>
    </Card>
  )
}

function Decision({ f, onChanged }: { f: FindingDetail; onChanged: () => void }) {
  return (
    <Card title="Decision" hint="a person decides; Sentri never marks a bug intended" className="decision">
      {!f.bugs.length ? (
        <p className="muted small">
          {f.verdict === 'unconfirmed'
            ? 'Nothing to decide: this candidate did not reproduce, so no bug was opened.'
            : 'No bug in the ledger is linked to this finding yet.'}
        </p>
      ) : (
        <div className="stack" style={{ gap: 20 }}>
          {f.bugs.map((b) => <BugDecision key={b.id} bug={b} onChanged={onChanged} showLink />)}
        </div>
      )}
    </Card>
  )
}
