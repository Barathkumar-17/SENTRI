import { useState } from 'react'
import { ChevronDown, ChevronRight } from 'lucide-react'
import { api, useApi, type Attempt, type Bug, type FindingDetail } from '../api'
import { BUG_STATUS, Badge, Banner, Empty, ErrorState, Loading, ORIGIN, PageHead, Panel, VERDICT, go, short, when } from '../ui'

const FILTERS: { id: string; label: string }[] = [
  { id: '', label: 'All' },
  { id: 'fail', label: 'Confirmed' },
  { id: 'flaky', label: 'Flaky' },
  { id: 'unconfirmed', label: 'Not reproduced' },
  { id: 'pass', label: 'Fixed' },
]

export function Findings({ id }: { id?: number }) {
  const [filter, setFilter] = useState('')
  const list = useApi(() => api.findings(filter || undefined), [filter])
  return (
    <>
      <PageHead
        title="Findings"
        context={<span>Candidates the tester flagged, each replayed 3 times and judged by plain code.</span>}
      />
      <div className="page-body">
        <div className="row" role="group" aria-label="Filter by verdict">
          {FILTERS.map((f) => (
            <button key={f.id || 'all'} className={`btn small${filter === f.id ? '' : ' ghost'}`} aria-pressed={filter === f.id} onClick={() => setFilter(f.id)}>
              {f.label}
            </button>
          ))}
        </div>
        <div className="split findings-split">
          <Panel title="All findings" hint={list.data ? `${list.data.length} shown` : undefined} flush>
            {list.loading && !list.data ? (
              <Loading label="Loading findings" />
            ) : list.error && !list.data ? (
              <ErrorState error={list.error} onRetry={list.reload} />
            ) : !list.data?.length ? (
              <Empty title={filter ? 'Nothing with this verdict' : 'No findings yet'}>
                {filter
                  ? 'Try another filter.'
                  : 'Findings appear after an agentic run flags a candidate. A run with none has not shown the app is safe.'}
              </Empty>
            ) : (
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr><th>Verdict</th><th>Finding</th><th className="hide-narrow">Found by</th></tr>
                  </thead>
                  <tbody>
                    {list.data.map((f) => {
                      const [tone, label] = VERDICT[f.verdict] ?? ['', f.verdict]
                      return (
                        <tr key={f.id} className="clickable" aria-selected={f.id === id} onClick={() => go(`/findings/${f.id}`)}>
                          <td><Badge tone={tone}>{label}</Badge></td>
                          <td>
                            <a href={`#/findings/${f.id}`} onClick={(e) => e.stopPropagation()}>{f.title || 'Untitled finding'}</a>
                            <span className="sub mono">{f.key ?? f.oracle_ids ?? ''}</span>
                            <span className="sub">run {f.run_id} · <span className="mono">{short(f.commit_sha)}</span></span>
                          </td>
                          <td className="hide-narrow">{ORIGIN[f.origin ?? ''] ?? f.origin ?? '–'}</td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </Panel>
          {id ? <FindingPanel id={id} /> : (
            <Panel><Empty title="Select a finding">Its evidence, replay attempts and the human decision appear here.</Empty></Panel>
          )}
        </div>
      </div>
    </>
  )
}

function FindingPanel({ id }: { id: number }) {
  const f = useApi(() => api.finding(id), [id])
  if (f.loading && !f.data) return <Panel><Loading label="Loading finding" /></Panel>
  if (f.error && !f.data) return <Panel><ErrorState error={f.error} onRetry={f.reload} /></Panel>
  return <FindingView f={f.data as FindingDetail} onChanged={f.reload} />
}

function FindingView({ f, onChanged }: { f: FindingDetail; onChanged: () => void }) {
  const [tone, label] = VERDICT[f.verdict] ?? ['', f.verdict]
  const hits = f.attempts.filter((a) => (a.oracles?.length ?? 0) > 0).length
  return (
    <div className="stack" aria-label="Finding detail">
      <Panel>
        <div className="stack">
          <div className="row"><Badge tone={tone}>{label}</Badge><span className="faint small">run <a href={`#/runs/${f.run_id}`}>{f.run_id}</a> · <span className="mono">{short(f.commit_sha)}</span> · {when(f.created_at)}</span></div>
          <h2 className="wrap-anywhere">{f.title || 'Untitled finding'}</h2>
          {f.key && <p className="mono xs muted wrap-anywhere">{f.key}</p>}
        </div>
      </Panel>

      <div className="trail" role="group" aria-label="Evidence trail">
        <div>
          <span className="label">Discovered by</span>
          <span className="big">{ORIGIN[f.origin ?? ''] ?? f.origin ?? 'not recorded'}</span>
          <span className="faint xs">{f.from_step ? `exploration steps ${f.from_step}–${f.to_step}` : 're-check of an earlier finding'}</span>
        </div>
        <div>
          <span className="label">Replays</span>
          <AttemptDots attempts={f.attempts} total={f.attempts.length || 3} />
          <span className="faint xs">{f.replay_state === 'none' ? 'on the live state (no reset)' : 'each from a restarted app'}</span>
        </div>
        <div>
          <span className="label">Confirmation</span>
          <span className="big">{label}</span>
          <span className="faint xs">{f.attempts.length ? `a break was seen in ${hits} of ${f.attempts.length} attempts` : 'no replay evidence stored'}</span>
        </div>
      </div>

      <Limitations f={f} />

      <Panel title="Steps replayed" hint={`${f.repro.length} steps; greyed = setup from earlier lines of attack`}>
        {!f.repro.length ? <Empty title="No steps stored" /> : (
          <ol className="steps">
            {f.repro.map((s, i) => (
              <li key={i} className={s.setup ? 'setup' : undefined}>
                <span className="n">{i + 1}</span>
                <div>
                  <div className="act">{describe(s.action)}</div>
                  {s.match && <div className="faint xs mono">re-matched: {s.match.method} {s.match.path}</div>}
                </div>
              </li>
            ))}
          </ol>
        )}
      </Panel>

      <Panel title="Replay evidence" hint="per attempt, as recorded">
        {!f.attempts.length ? <Empty title="No replay evidence">Findings recorded before evidence linking have none.</Empty> : (
          <div className="stack">{f.attempts.map((a) => <AttemptView key={a.attempt} a={a} />)}</div>
        )}
        {f.notes.length > 0 && (
          <div className="stack" style={{ marginTop: 16 }}>
            <h3>Replay notes</h3>
            <pre className="code">{f.notes.join('\n')}</pre>
          </div>
        )}
      </Panel>

      <BugsPanel bugs={f.bugs} onChanged={onChanged} />
    </div>
  )
}

function AttemptDots({ attempts, total }: { attempts: Attempt[]; total: number }) {
  return (
    <div className="attempts" aria-label="Replay attempts">
      {Array.from({ length: total }, (_, i) => {
        const a = attempts[i]
        const state = !a || a.missing ? '' : a.completed === false ? 'failed' : (a.oracles?.length ?? 0) > 0 ? 'hit' : 'quiet'
        const words = !a || a.missing ? 'no evidence' : state === 'failed' ? 'could not replay' : state === 'hit' ? 'break seen' : 'nothing seen'
        return <span key={i} className={`attempt-dot ${state}`} title={`Attempt ${i + 1}: ${words}`} aria-label={`Attempt ${i + 1}: ${words}`}>{i + 1}</span>
      })}
    </div>
  )
}

function AttemptView({ a }: { a: Attempt }) {
  const [open, setOpen] = useState(false)
  if (a.missing) return <Banner tone="warn" title={`Attempt ${a.attempt}: evidence file missing`}>It was recorded but is no longer on disk.</Banner>
  const Icon = open ? ChevronDown : ChevronRight
  return (
    <div className="stack" style={{ gap: 8 }}>
      <div className="row">
        <strong className="small">Attempt {a.attempt}</strong>
        {a.completed === false ? <Badge tone="warn">Could not replay every step</Badge> : (a.oracles?.length ?? 0) > 0 ? <Badge tone="bad">Break seen</Badge> : <Badge tone="ok">Nothing seen</Badge>}
        <button className="btn ghost small right" aria-expanded={open} onClick={() => setOpen(!open)}>
          <Icon size={14} aria-hidden="true" /> {open ? 'Hide' : 'Show'} responses
        </button>
      </div>
      {a.oracles?.map((o, i) => <p key={i} className="small"><span className="mono">{o.oracle_id}</span> <span className="muted">{o.detail}</span></p>)}
      {a.notes?.map((n, i) => <p key={i} className="xs faint">{n}</p>)}
      {open && (
        <>
          <pre className="code nowrap">{(a.responses ?? []).map((r) => `${r.identity.padEnd(5)} ${r.method.padEnd(6)} ${String(r.status ?? '–').padEnd(4)} ${r.path}`).join('\n') || 'no responses recorded'}</pre>
          {(a.server_log_tail?.length ?? 0) > 0 && (
            <>
              <p className="xs faint">Server log tail, for you only: no verdict reads server logs (black box).</p>
              <pre className="code">{a.server_log_tail!.join('\n')}</pre>
            </>
          )}
        </>
      )}
    </div>
  )
}

function Limitations({ f }: { f: FindingDetail }) {
  const items: string[] = []
  if (f.replay_state === 'none') items.push('Replayed on the live state: the app was not reset between attempts, so they are not independent confirmations.')
  if (f.replay_state === 'restart') items.push('Each attempt restarted the app; state kept outside its cleaned files (an external database) survives a restart.')
  if (f.origin === 'code_probe') items.push("Found by one of Sentri's own probes, not by the model's choice of action.")
  if (f.origin === 'ai_suspicion') items.push('Raised by the model without a measured signal; only the replay verdict counts.')
  if (f.verdict === 'unconfirmed') items.push('Not reproduced in replay: this is not evidence that the behaviour is correct.')
  if (f.attempts.some((a) => a.completed === false)) items.push('At least one attempt could not run every step, so it neither confirms nor clears the finding.')
  if (!items.length) return null
  return (
    <Panel title="Limitations">
      <ul className="small muted" style={{ margin: 0, paddingLeft: 18, display: 'grid', gap: 6 }}>{items.map((t) => <li key={t}>{t}</li>)}</ul>
    </Panel>
  )
}

function BugsPanel({ bugs, onChanged }: { bugs: Bug[]; onChanged: () => void }) {
  if (!bugs.length) return null
  return (
    <Panel title="Bugs in the ledger" hint="a person decides; Sentri never marks a bug intended">
      <div className="stack">{bugs.map((b) => <BugDecision key={b.id} bug={b} onChanged={onChanged} />)}</div>
    </Panel>
  )
}

export function BugDecision({ bug, onChanged }: { bug: Bug; onChanged: () => void }) {
  const [tone, label] = BUG_STATUS[bug.status] ?? ['', bug.status]
  const [note, setNote] = useState('')
  const [by, setBy] = useState(() => localStorage.getItem('sentri.name') ?? '')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const target = bug.status === 'intended' ? 'open' : 'intended'
  const submit = async () => {
    setBusy(true)
    setErr(null)
    try {
      localStorage.setItem('sentri.name', by)
      await api.setBugStatus(bug.id, target, note, by || 'dashboard')
      setNote('')
      onChanged()
    } catch (e) {
      setErr((e as Error).message)
    } finally {
      setBusy(false)
    }
  }
  return (
    <div className="stack" style={{ gap: 10 }}>
      <div className="row"><span className="mono small">{bug.id}</span><Badge tone={tone}>{label}</Badge><span className="mono xs faint">{bug.oracle_id}</span>{bug.regressions > 0 && <Badge tone="bad">regressed {bug.regressions}×</Badge>}</div>
      {bug.sample_detail && <p className="small muted wrap-anywhere">{bug.sample_detail}</p>}
      {bug.human_note && <p className="xs faint">Note: {bug.human_note}</p>}
      <div className="form-grid">
        <div className="field">
          <label htmlFor={`note-${bug.id}`}>{target === 'intended' ? 'Why is this intended?' : 'Why reopen it?'}</label>
          <input id={`note-${bug.id}`} className="input" value={note} onChange={(e) => setNote(e.target.value)} placeholder={target === 'intended' ? 'Required: the reason, for the record' : 'Optional'} />
        </div>
        <div className="field">
          <label htmlFor={`by-${bug.id}`}>Your name</label>
          <input id={`by-${bug.id}`} className="input" value={by} onChange={(e) => setBy(e.target.value)} placeholder="Recorded with the decision" />
        </div>
      </div>
      <div className="row">
        <button className="btn small" disabled={busy || (target === 'intended' && !note.trim())} onClick={submit}>
          {target === 'intended' ? 'Mark intended' : 'Reopen'}
        </button>
        {err && <span className="small" role="alert" style={{ color: 'var(--bad)' }}>{err}</span>}
      </div>
    </div>
  )
}

function describe(action: Record<string, unknown>): string {
  const { kind, ...rest } = action
  const args = Object.entries(rest)
    .filter(([, v]) => v !== null && v !== undefined && v !== '' && !(Array.isArray(v) && !v.length))
    .map(([k, v]) => `${k}=${typeof v === 'string' ? JSON.stringify(v.length > 120 ? v.slice(0, 117) + '…' : v) : JSON.stringify(v)}`)
  return `${kind}(${args.join(', ')})`
}

