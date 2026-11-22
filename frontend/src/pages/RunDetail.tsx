import { Fragment, useEffect, useMemo, useState } from 'react'
import { api, useApi, useEventStream, type Run, type SentriEvent } from '../api'
import {
  Badge, Banner, CHECK, Empty, ErrorState, Loading, ORIGIN, PageHead, Panel, Readiness, RunStatus, Tabs, VERDICT,
  READINESS_NOTE, cost, count, duration, readinessBasis, go, num, quotaStopped, short, when,
} from '../ui'

// Events worth weight in the feed; the rest read as ordinary progress.
const KEY_EVENTS = new Set(['finding', 'candidate', 'bug_opened', 'regression', 'warning', 'run_finished', 'run_aborted', 'llm_fallback'])

type Tab = 'activity' | 'findings' | 'exploration' | 'baseline' | 'usage' | 'coverage'

export function RunDetail({ id }: { id: number }) {
  const run = useApi(() => api.run(id), [id], 4000)
  const [tab, setTab] = useState<Tab>('activity')
  if (run.loading && !run.data) return <Frame id={id}><Loading label="Loading run" /></Frame>
  if (run.error && !run.data) return <Frame id={id}><ErrorState error={run.error} onRetry={run.reload} /></Frame>
  const r = run.data as Run
  return (
    <>
      <PageHead
        crumb={<><a href="#/runs">Runs</a> / Run {r.id}</>}
        title={`Run ${r.id}`}
        context={
          <>
            <RunStatus run={r} />
            <span className="mono">{short(r.commit_sha)}</span>
            <span className="mono">{r.branch ?? 'local'}</span>
            <span>started {when(r.started_at)}</span>
            {r.duration_s != null && <span>took {duration(r.duration_s)}</span>}
          </>
        }
      />
      <div className="page-body">
        {run.error && <Banner tone="bad" title="Lost contact with the Sentri server">{run.error.message} Showing the last data received.</Banner>}
        <Outcome run={r} />
        <Tabs<Tab>
          value={tab}
          onChange={setTab}
          tabs={[
            { id: 'activity', label: 'Activity' },
            { id: 'findings', label: 'Findings', count: r.coverage?.agentic?.candidates },
            { id: 'exploration', label: 'Exploration', count: r.coverage?.agentic?.steps },
            { id: 'baseline', label: 'Baseline checks' },
            { id: 'usage', label: 'Model use', count: r.usage?.calls },
            { id: 'coverage', label: 'Coverage' },
          ]}
        />
        {tab === 'activity' && <Activity run={r} />}
        {tab === 'findings' && <RunFindings id={id} />}
        {tab === 'exploration' && <Exploration id={id} />}
        {tab === 'baseline' && <Baseline id={id} />}
        {tab === 'usage' && <UsagePanel run={r} />}
        {tab === 'coverage' && <CoveragePanel run={r} />}
      </div>
    </>
  )
}

function Frame({ id, children }: { id: number; children: React.ReactNode }) {
  return (
    <>
      <PageHead crumb={<><a href="#/runs">Runs</a> / Run {id}</>} title={`Run ${id}`} />
      <div className="page-body"><Panel>{children}</Panel></div>
    </>
  )
}

/** Observed impact and coverage side by side, and how the run ended. Never a pass for untested ground. */
function Outcome({ run }: { run: Run }) {
  const b = run.breakdown
  const ag = run.coverage?.agentic
  const stop = quotaStopped(run)
  const insufficient = b?.status === 'insufficient_coverage' || b?.score == null
  return (
    <div className="stack">
      {run.status === 'aborted' && <Banner tone="bad" title="The run was aborted">See the activity log for the reason (for example, the app did not start or a flow could not complete).</Banner>}
      {stop && (
        <Banner tone="warn" title="Model use stopped before exploration finished">
          <span className="mono">{stop}</span>. Only what ran before the stop is reported below; it is not a full test.
        </Banner>
      )}
      {run.coverage?.replay_state === 'none' && (
        <Banner tone="info" title="Replays ran on the app's live state">Sentri did not start this app (URL mode), so it could not reset it between replays; three replays are not three independent confirmations.</Banner>
      )}
      <div className="facts">
        <div className="fact">
          <span className="label">Readiness</span>
          <span className="value"><Readiness breakdown={b} /></span>
          <span className="note">{insufficient ? 'No applicable baseline check ran, so there is no score.' : readinessBasis(b)}</span>
        </div>
        <div className="fact">
          <span className="label">Confirmed findings</span>
          <span className="value">{ag ? num(ag.confirmed) : '–'}</span>
          <span className="note">{ag ? `${count(ag.candidates ?? 0, 'candidate')}, ${num(ag.unconfirmed)} not reproduced, ${num(ag.inconclusive)} inconclusive` : 'no agentic phase'}</span>
        </div>
        <div className="fact">
          <span className="label">Exploration</span>
          <span className="value small">{ag ? [count(ag.steps ?? 0, 'step'), count(ag.distinct_states, 'state')].filter(Boolean).join(', ') : '–'}</span>
          <span className="note">{ag?.exploration_stop ? ag.exploration_stop.startsWith('stopped') || ag.exploration_stop.startsWith('model use') ? ag.exploration_stop : `stopped: ${ag.exploration_stop}` : run.coverage?.completion?.phases?.agentic ?? ''}</span>
        </div>
        <div className="fact">
          <span className="label">Model use</span>
          <span className="value small">{run.usage ? `${num(run.usage.calls)} calls` : '–'}</span>
          <span className="note">{run.usage ? `${num(run.usage.input_tokens)} in + ${num(run.usage.output_tokens)} out tokens · ${cost(run.usage.cost_usd, run.usage.calls)}` : ''}</span>
        </div>
      </div>
      {b?.formula && <p className="faint xs mono wrap-anywhere">{b.formula}</p>}
      <p className="footnote">{READINESS_NOTE}</p>
    </div>
  )
}

function Activity({ run }: { run: Run }) {
  const live = run.status === 'running'
  const history = useApi(() => api.events(run.id), [run.id])
  const [extra, setExtra] = useState<SentriEvent[]>([])
  const stream = useEventStream(run.id, live, (e) => setExtra((xs) => (xs.some((x) => x.id === e.id) ? xs : [...xs, e])))
  const events = useMemo(() => {
    const seen = new Set<number>()
    return [...(history.data ?? []), ...extra].filter((e) => (seen.has(e.id) ? false : (seen.add(e.id), true)))
  }, [history.data, extra])
  const [shot, setShot] = useState(0)
  useEffect(() => {
    if (!live) return
    const t = setInterval(() => setShot((n) => n + 1), 2000)
    return () => clearInterval(t)
  }, [live])
  return (
    <div className={live ? 'split' : 'stack'}>
      <Panel
        title="Activity"
        hint={live ? <span className="row"><span className={`dot ${stream === 'open' ? 'on' : stream === 'disconnected' ? 'off' : ''}`} aria-hidden="true" />{stream === 'open' ? 'live' : stream === 'connecting' ? 'connecting…' : 'disconnected'}</span> : `${events.length} events`}
        flush
      >
        {live && stream === 'disconnected' && <div className="panel-body"><Banner tone="bad" title="Live updates disconnected">The event stream closed; the run may still be going. Reload the page to reconnect.</Banner></div>}
        {history.loading && !history.data ? (
          <Loading label="Loading activity" />
        ) : history.error && !history.data ? (
          <ErrorState error={history.error} onRetry={history.reload} />
        ) : !events.length ? (
          <Empty title="No events recorded">This run left no activity log.</Empty>
        ) : (
          <ol className="feed" aria-live={live ? 'polite' : undefined}>
            {events.map((e) => (
              <li key={e.id}>
                <span className="t">{new Date(e.ts).toLocaleTimeString(undefined, { hour12: false })}</span>
                <span className="m">
                  {KEY_EVENTS.has(e.kind) ? <strong>{e.message}</strong> : e.message}
                  {e.reason && <span className="r">{e.reason}</span>}
                </span>
              </li>
            ))}
          </ol>
        )}
      </Panel>
      {live && (
        <Panel title="Live view" hint="the browser Sentri is driving, every 2 s">
          <img className="live-shot" src={`/api/live/screenshot.jpg?n=${shot}`} alt="Current page in the browser Sentri is driving" />
        </Panel>
      )}
    </div>
  )
}

function RunFindings({ id }: { id: number }) {
  const ag = useApi(() => api.agentic(id), [id])
  if (ag.loading && !ag.data) return <Panel><Loading label="Loading findings" /></Panel>
  if (ag.error && !ag.data) return <Panel><ErrorState error={ag.error} onRetry={ag.reload} /></Panel>
  const findings = ag.data?.findings ?? []
  return (
    <Panel title="Findings of this run" hint="candidates replayed 3 times, judged by plain code" flush>
      {!findings.length ? (
        <Empty title="No candidates in this run">Exploration flagged nothing to replay. That is not evidence the app is safe; see Coverage for what was actually tried.</Empty>
      ) : (
        <div className="table-wrap">
          <table>
            <thead><tr><th>Verdict</th><th>Finding</th><th>Found by</th><th>Oracles</th></tr></thead>
            <tbody>
              {findings.map((f) => {
                const [tone, label] = VERDICT[f.verdict] ?? ['', f.verdict]
                return (
                  <tr key={f.id} className="clickable" onClick={() => go(`/findings/${f.id}`)}>
                    <td><Badge tone={tone}>{label}</Badge></td>
                    <td><a href={`#/findings/${f.id}`} onClick={(e) => e.stopPropagation()}>{f.title || 'Untitled finding'}</a></td>
                    <td>{ORIGIN[f.origin ?? ''] ?? f.origin ?? '–'}</td>
                    <td className="mono xs">{f.oracle_ids || '–'}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  )
}

function Exploration({ id }: { id: number }) {
  const ag = useApi(() => api.agentic(id), [id])
  if (ag.loading && !ag.data) return <Panel><Loading label="Loading exploration" /></Panel>
  if (ag.error && !ag.data) return <Panel><ErrorState error={ag.error} onRetry={ag.reload} /></Panel>
  const steps = ag.data?.steps ?? []
  const kind = ag.data?.strategy?.strategy?.app_kind
  return (
    <Panel title="Exploration steps" hint={kind ? `strategy: ${kind}` : undefined} flush>
      {!steps.length ? (
        <Empty title="No exploration in this run">The agentic phase did not run (no model, baseline mode) or stopped before its first step.</Empty>
      ) : (
        <ol className="steps" style={{ padding: '0 20px' }}>
          {steps.map((s) => (
            <li key={s.step}>
              <span className="n">{s.step}</span>
              <div>
                <div className="act">[{s.identity ?? '?'}] {s.action_text}</div>
                {s.thought && <div className="why">{s.thought}</div>}
                <div className="meta">
                  <Badge tone="outline">{ORIGIN[s.origin ?? ''] ?? s.origin ?? 'origin not recorded'}</Badge>
                  {!s.ok && <Badge tone="warn">{s.error ?? 'failed'}</Badge>}
                  {s.signals?.map((sig, i) => <Badge key={i} tone="bad">{sig.oracle_id}</Badge>)}
                </div>
              </div>
            </li>
          ))}
        </ol>
      )}
    </Panel>
  )
}

function Baseline({ id }: { id: number }) {
  const grid = useApi(() => api.grid(id), [id])
  if (grid.loading && !grid.data) return <Panel><Loading label="Loading checks" /></Panel>
  if (grid.error && !grid.data) return <Panel><ErrorState error={grid.error} onRetry={grid.reload} /></Panel>
  const g = grid.data
  if (!g?.cells.length)
    return <Panel title="Baseline checks"><Empty title="No baseline checks in this run">The contract has no attacked flows, or the run ran in agentic mode only. Without them there is no readiness score.</Empty></Panel>
  return (
    <Panel title="Baseline checks" hint="each flow × fixed attack, 3 attempts" flush>
      <div className="table-wrap">
        <table>
          <thead><tr><th>Flow</th>{g.attacks.map((a) => <th key={a} className="mono">{a}</th>)}</tr></thead>
          <tbody>
            {g.flows.map((f) => (
              <tr key={f}>
                <td className="mono">{f}</td>
                {g.attacks.map((a) => {
                  const c = g.cells.find((x) => x.flow_id === f && x.attack_id === a)
                  if (!c) return <td key={a} className="faint">–</td>
                  const [tone, label] = CHECK[c.score_verdict] ?? ['', c.score_verdict]
                  return <td key={a}><Badge tone={tone} title={c.oracle_hits || undefined}>{label}{c.reused ? ' (reused)' : ''}</Badge></td>
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Panel>
  )
}

function UsagePanel({ run }: { run: Run }) {
  const u = run.usage
  if (!u) return <Panel title="Model use"><Empty title="No usage recorded">Runs from before usage tracking, or without a model, have none.</Empty></Panel>
  const roles = Object.entries(u.roles ?? {})
  return (
    <Panel title="Model use" hint={`${u.provider ?? 'provider'} · ${cost(u.cost_usd, u.calls)}`} flush>
      <div className="table-wrap">
        <table>
          <thead><tr><th>Role</th><th className="num">Calls</th><th className="num">Failed</th><th className="num">Retries</th><th className="num">Input tokens</th><th className="num">Output tokens</th><th className="num">Time</th></tr></thead>
          <tbody>
            {roles.map(([role, r]) => (
              <tr key={role}><td>{role}</td><td className="num">{num(r.calls)}</td><td className="num">{num(r.failed)}</td><td className="num">{num(r.retries)}</td><td className="num">{num(r.input_tokens)}</td><td className="num">{num(r.output_tokens)}</td><td className="num">{duration(r.seconds)}</td></tr>
            ))}
            <tr><td><strong>Total</strong></td><td className="num">{num(u.calls)}</td><td className="num">{num(u.failed_calls)}</td><td className="num">{num(u.retries)}</td><td className="num">{num(u.input_tokens)}</td><td className="num">{num(u.output_tokens)}</td><td /></tr>
          </tbody>
        </table>
      </div>
      <div className="panel-body">
        <dl className="kv">
          <dt>Cost</dt><dd>{cost(u.cost_usd, u.calls)}{u.unpriced_calls ? ` (${u.unpriced_calls} calls to a model without a known price)` : ''}</dd>
          <dt>Stop reason</dt><dd>{u.stop_reason ?? 'none: model use was not stopped by a limit'}</dd>
          {u.refused_calls ? <><dt>Refused calls</dt><dd>{u.refused_calls}</dd></> : null}
        </dl>
      </div>
    </Panel>
  )
}

function CoveragePanel({ run }: { run: Run }) {
  const c = run.coverage
  if (!c) return <Panel title="Coverage"><Empty title="No coverage recorded">Runs from before coverage tracking have none.</Empty></Panel>
  const checks = c.baseline?.checks ?? {}
  const ag = c.agentic
  return (
    <div className="split">
      <Panel title="Baseline suite">
        <dl className="kv">
          {Object.entries(checks).map(([k, v]) => <Fragment key={k}><dt>{k.replace('_', ' ')}</dt><dd>{num(v)}</dd></Fragment>)}
          <dt>Flows tested</dt><dd className="mono">{c.baseline?.flows?.join(', ') || 'none'}</dd>
          <dt>Follow-up probes</dt><dd>{num(c.baseline?.probes)}</dd>
        </dl>
      </Panel>
      <Panel title="Exploration">
        {ag ? (
          <dl className="kv">
            <dt>Steps</dt><dd>{num(ag.steps)} ({num(ag.ai_steps)} model, {num(ag.code_probe_steps)} probes, {num(ag.fallback_steps)} fallbacks, {num(ag.invalid_steps)} invalid)</dd>
            <dt>Distinct states</dt><dd>{num(ag.distinct_states)}{ag.states_by_identity ? ` (${Object.entries(ag.states_by_identity).map(([k, v]) => `${k} ${v}`).join(', ')})` : ''}</dd>
            <dt>Recon</dt><dd>{num(ag.pages)} pages, {num(ag.endpoints)} endpoints</dd>
            <dt>Untried options left</dt><dd>{num(ag.untried_options_left)}</dd>
            <dt>Dead ends refused</dt><dd>{num(ag.known_dead_end_refusals)}</dd>
            <dt>Stopped</dt><dd>{ag.exploration_stop || '–'}</dd>
          </dl>
        ) : (
          <Empty title="No exploration">This run did not include the agentic phase.</Empty>
        )}
      </Panel>
      <Panel title="Completion">
        <dl className="kv">
          {Object.entries(c.completion?.phases ?? {}).map(([k, v]) => <Fragment key={k}><dt>{k}</dt><dd>{v}</dd></Fragment>)}
          <dt>Model stop</dt><dd>{c.completion?.model_stop ?? 'none'}</dd>
          <dt>Replay state</dt><dd>{c.replay_state === 'none' ? 'live state (URL mode, no reset)' : 'restarted app per attempt'}</dd>
        </dl>
      </Panel>
    </div>
  )
}
