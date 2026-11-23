import { useEffect, useMemo, useState } from 'react'
import { ArrowRight } from 'lucide-react'
import { api, useApi, useEventStream, type Run, type SentriEvent } from '../api'
import {
  Badge, Banner, Card, Empty, ErrorState, Loading, ORIGIN, PageHead, RunStatus, Tabs, cost, count, duration, href, num,
  quotaStopped, short, when,
} from '../ui'

// Events worth weight in the feed; the rest read as ordinary progress.
const KEY_EVENTS = new Set(['finding', 'candidate', 'bug_opened', 'regression', 'warning', 'run_finished', 'run_aborted', 'llm_fallback'])

type Tab = 'activity' | 'exploration' | 'usage'

export function RunDetail({ id }: { id: number }) {
  const run = useApi(() => api.run(id), [id], 4000)
  const [tab, setTab] = useState<Tab>('activity')
  const crumbs = [{ label: 'Runs', href: '#/runs' }, { label: `Run ${id}` }]
  if (run.loading && !run.data) return <Frame crumbs={crumbs} id={id}><Loading label="Loading run" /></Frame>
  if (run.error && !run.data) return <Frame crumbs={crumbs} id={id}><ErrorState error={run.error} onRetry={run.reload} what="run" /></Frame>
  const r = run.data as Run
  const live = r.status === 'running'
  return (
    <>
      <PageHead
        crumbs={crumbs}
        title={`Run ${r.id}`}
        purpose={live ? 'This run is in progress. Follow it here; its report fills in as it finishes.' : 'How this run went: its progress, how it ended and what it did, step by step.'}
        meta={
          <>
            <RunStatus run={r} />
            <span className="mono">{short(r.commit_sha)}</span>
            <span>{r.branch ?? 'local'}</span>
            <span>started {when(r.started_at)}</span>
            {r.duration_s != null && <span>took {duration(r.duration_s)}</span>}
          </>
        }
        action={
          <a className="btn primary" href={href(`runs/${r.id}/report`)}>
            {live ? 'Report so far' : 'Open report'} <ArrowRight size={15} aria-hidden="true" />
          </a>
        }
      />
      <div className="page-body">
        {run.error && <Banner tone="bad" title="Lost contact with the Sentri server">{run.error.message} Showing the last data received.</Banner>}
        <Ending run={r} />
        <Progress run={r} />
        <Tabs<Tab>
          label="Run details"
          value={tab}
          onChange={setTab}
          tabs={[
            { id: 'activity', label: 'Activity' },
            { id: 'exploration', label: 'Exploration steps', count: r.coverage?.agentic?.steps },
            { id: 'usage', label: 'Model use', count: r.usage?.calls },
          ]}
        />
        {tab === 'activity' && <Activity run={r} />}
        {tab === 'exploration' && <Exploration id={id} />}
        {tab === 'usage' && <UsagePanel run={r} />}
      </div>
    </>
  )
}

function Frame({ id, crumbs, children }: { id: number; crumbs: { label: string; href?: string }[]; children: React.ReactNode }) {
  return (
    <>
      <PageHead crumbs={crumbs} title={`Run ${id}`} />
      <div className="page-body"><Card>{children}</Card></div>
    </>
  )
}

/** How the run ended, when that is not simply "finished": aborted, model use stopped early, live-state replays. */
function Ending({ run }: { run: Run }) {
  const stop = quotaStopped(run)
  return (
    <>
      {run.status === 'aborted' && <Banner tone="bad" title="The run was aborted">See the activity below for the reason (for example, the app did not start or a flow could not complete).</Banner>}
      {stop && (
        <Banner tone="warn" title="Model use stopped before exploration finished">
          <span className="mono">{stop}</span>. Only what ran before the stop is reported; it is not a full test.
        </Banner>
      )}
      {run.coverage?.replay_state === 'none' && (
        <Banner tone="info" title="Replays ran on the app's live state">Sentri did not start this app (URL mode), so it could not reset it between replays; three replays are not three independent confirmations.</Banner>
      )}
    </>
  )
}

/** Phases as recorded at the end of the run, or as far as the event log shows while it is still going. */
function Progress({ run }: { run: Run }) {
  const events = useApi(() => api.events(run.id), [run.id], run.status === 'running' ? 4000 : undefined)
  const ev = events.data ?? []
  const steps = ev.filter((e) => e.kind === 'explore_step').length
  const candidates = ev.filter((e) => e.kind === 'candidate').length
  const checks = ev.filter((e) => e.kind === 'check_finished' || e.kind === 'check_reused').length
  const lastPhase = [...ev].reverse().find((e) => e.kind === 'phase')
  const phases = run.coverage?.completion?.phases
  return (
    <Card title="Progress" hint={run.status === 'running' ? 'updates every few seconds' : undefined}>
      {phases ? (
        <ol className="phases">
          {Object.entries(phases).map(([name, state]) => {
            const cls = /quota|limit|budget|stopped/.test(state) ? 'stopped' : /^done/.test(state) ? 'done' : /skipped|not run|no baseline/.test(state) ? 'skipped' : ''
            return (
              <li key={name} className={`phase ${cls}`}>
                <span className="name">{name}</span>
                <span className="state-text">{state}</span>
              </li>
            )
          })}
        </ol>
      ) : (
        <ol className="phases">
          <li className={`phase ${run.status === 'running' ? 'running' : ''}`}>
            <span className="name">Current phase</span>
            <span className="state-text">{lastPhase?.message ?? (run.status === 'running' ? 'starting' : 'not recorded')}</span>
          </li>
          <li className="phase"><span className="name">Exploration steps</span><span className="state-text">{num(steps)}</span></li>
          <li className="phase"><span className="name">Candidates to replay</span><span className="state-text">{num(candidates)}</span></li>
          <li className="phase"><span className="name">Baseline checks done</span><span className="state-text">{num(checks)}</span></li>
        </ol>
      )}
    </Card>
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
    <div className={live ? 'grid-2' : 'stack'}>
      <Card
        title="Activity"
        hint={live ? <span className="row" style={{ gap: 6 }}><span className={`dot ${stream === 'open' ? 'on' : stream === 'disconnected' ? 'off' : ''}`} aria-hidden="true" />{stream === 'open' ? 'live' : stream === 'connecting' ? 'connecting…' : 'disconnected'}</span> : count(events.length, 'event')}
        flush
      >
        {live && stream === 'disconnected' && <div className="card-body"><Banner tone="bad" title="Live updates disconnected">The event stream closed; the run may still be going. Reload the page to reconnect.</Banner></div>}
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
      </Card>
      {live && (
        <Card title="Live view" hint="the browser Sentri is driving, every 2 s">
          <img className="live-shot" src={`/api/live/screenshot.jpg?n=${shot}`} alt="Current page in the browser Sentri is driving" />
        </Card>
      )}
    </div>
  )
}

function Exploration({ id }: { id: number }) {
  const ag = useApi(() => api.agentic(id), [id])
  if (ag.loading && !ag.data) return <Card><Loading label="Loading exploration" /></Card>
  if (ag.error && !ag.data) return <Card><ErrorState error={ag.error} onRetry={ag.reload} /></Card>
  const steps = ag.data?.steps ?? []
  const kind = ag.data?.strategy?.strategy?.app_kind
  return (
    <Card title="Exploration steps" hint={kind ? `strategy: ${kind}` : 'what the tester did, in order'}>
      {!steps.length ? (
        <Empty title="No exploration in this run">The agentic phase did not run (no model, or baseline mode) or stopped before its first step.</Empty>
      ) : (
        <ol className="steps">
          {steps.map((s) => (
            <li key={s.step}>
              <span className="n">{s.step}</span>
              <div>
                <div className="act">[{s.identity ?? '?'}] {s.action_text}</div>
                {s.thought && <div className="why">{s.thought}</div>}
                <div className="meta">
                  <Badge>{ORIGIN[s.origin ?? ''] ?? s.origin ?? 'origin not recorded'}</Badge>
                  {!s.ok && <Badge tone="warn">{s.error ?? 'failed'}</Badge>}
                  {s.signals?.map((sig, i) => <Badge key={i} tone="bad">{sig.oracle_id}</Badge>)}
                </div>
              </div>
            </li>
          ))}
        </ol>
      )}
    </Card>
  )
}

function UsagePanel({ run }: { run: Run }) {
  const u = run.usage
  if (!u) return <Card title="Model use"><Empty title="No usage recorded">Runs from before usage tracking, or without a model, have none.</Empty></Card>
  const roles = Object.entries(u.roles ?? {})
  return (
    <Card title="Model use" hint={`${u.provider ?? 'provider'} · ${cost(u.cost_usd, u.calls)}`} flush foot={<dl className="kv">
      <dt>Cost</dt><dd>{cost(u.cost_usd, u.calls)}{u.unpriced_calls ? ` (${u.unpriced_calls} calls to a model without a known price)` : ''}</dd>
      <dt>Stop reason</dt><dd>{u.stop_reason ?? 'none: model use was not stopped by a limit'}</dd>
      {u.refused_calls ? <><dt>Refused calls</dt><dd>{u.refused_calls}</dd></> : null}
    </dl>}>
      {!roles.length ? <Empty title="No model calls">This run used no model.</Empty> : (
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
      )}
    </Card>
  )
}
