import { ArrowRight, Plus } from 'lucide-react'
import { api, useApi, type Run } from '../api'
import {
  BugBadge, Card, Empty, ErrorState, Loading, PageHead, Readiness, RunStatus, VerdictBadge, count, duration, href,
  num, outcomeLine, quotaStopped, readinessBasis, short, when,
} from '../ui'
import { GettingStarted } from './GettingStarted'

const NewRunButton = () => (
  <a className="btn primary" href="#/new">
    <Plus size={16} aria-hidden="true" /> New run
  </a>
)

export function Home() {
  const runs = useApi(api.runs, [], 8000)
  if (runs.loading && !runs.data) {
    return (
      <>
        <PageHead title="Sentri" purpose="Your latest results and what needs a decision." action={<NewRunButton />} />
        <div className="page-body"><Card><Loading label="Loading runs" /></Card></div>
      </>
    )
  }
  if (runs.error && !runs.data) {
    return (
      <>
        <PageHead title="Sentri" purpose="Your latest results and what needs a decision." action={<NewRunButton />} />
        <div className="page-body"><Card><ErrorState error={runs.error} onRetry={runs.reload} /></Card></div>
      </>
    )
  }
  if (!runs.data?.length) {
    return (
      <>
        <PageHead
          title="Welcome to Sentri"
          purpose="Sentri attacks your running web app like a hostile or careless user, so it breaks here instead of in a demo. Nothing has been tested from this machine yet: set up once, then start your first run."
          action={<NewRunButton />}
        />
        <div className="page-body">
          <GettingStarted embedded />
        </div>
      </>
    )
  }
  const list = runs.data
  return (
    <>
      <PageHead title="Sentri" purpose="Your latest results and what needs a decision." action={<NewRunButton />} />
      <div className="page-body">
        <div className="grid-main">
          <div className="stack" style={{ gap: 20 }}>
            <LatestRun run={list[0]} />
            <RecentRuns runs={list.slice(0, 6)} total={list.length} />
          </div>
          <div className="stack" style={{ gap: 20 }}>
            <NeedsDecision />
            <RecentFindings />
            <Card title="Help">
              <p className="small muted">Install steps, how Sentri reaches your app, and how to keep keys out of files.</p>
              <p style={{ marginTop: 8 }}><a href="#/start">Getting started</a></p>
            </Card>
          </div>
        </div>
      </div>
    </>
  )
}

/** The latest run's outcome in one sentence, qualified by coverage and completion, with the way to its report. */
function LatestRun({ run }: { run: Run }) {
  const b = run.breakdown
  const ag = run.coverage?.agentic
  const stop = quotaStopped(run)
  return (
    <Card
      title={`Latest run · Run ${run.id}`}
      hint={<span className="row" style={{ gap: 8 }}><RunStatus run={run} /><span className="mono">{short(run.commit_sha)}</span>{run.branch ?? 'local'} · {when(run.started_at)}</span>}
      foot={
        <span className="row">
          <a className="btn small primary" href={href(`runs/${run.id}/report`)}>Open report <ArrowRight size={14} aria-hidden="true" /></a>
          <a className="btn small" href={href(`runs/${run.id}`)}>Activity</a>
        </span>
      }
    >
      <div className="stack">
        <p className="outcome-line">{outcomeLine(run)}</p>
        <div className="facts">
          <div className="fact">
            <span className="label">Readiness</span>
            <span className="value"><Readiness breakdown={b} /></span>
            <span className="note">{readinessBasis(b)}</span>
          </div>
          <div className="fact">
            <span className="label">Exploration</span>
            <span className="value">{ag ? count(ag.steps ?? 0, 'step') : 'not run'}</span>
            <span className="note">{ag ? `${count(ag.candidates ?? 0, 'candidate')} replayed` : run.coverage?.completion?.phases?.agentic ?? 'no agentic phase recorded'}</span>
          </div>
          <div className="fact">
            <span className="label">Completion</span>
            <span className="value">{stop ? 'Stopped early' : run.status === 'finished' ? 'Ran to the end' : run.status === 'running' ? 'In progress' : 'Aborted'}</span>
            <span className="note">{stop ?? (run.duration_s != null ? `took ${duration(run.duration_s)}` : '')}</span>
          </div>
        </div>
      </div>
    </Card>
  )
}

function RecentRuns({ runs, total }: { runs: Run[]; total: number }) {
  return (
    <Card title="Recent runs" hint={total > runs.length ? `${runs.length} of ${total}` : undefined} action={<a className="btn small ghost" href="#/runs">All runs</a>} flush>
      <div className="table-wrap">
        <table>
          <thead>
            <tr><th>Run</th><th>Readiness</th><th className="num">Confirmed</th><th>Started</th></tr>
          </thead>
          <tbody>
            {runs.map((r) => (
              <tr key={r.id}>
                <td><a href={href(`runs/${r.id}/report`)}>Run {r.id}</a><span className="sub"><span className="mono">{short(r.commit_sha)}</span> · {r.branch ?? 'local'}</span></td>
                <td><Readiness breakdown={r.breakdown} /></td>
                <td className="num">{r.coverage?.agentic ? num(r.coverage.agentic.confirmed) : '–'}</td>
                <td className="faint">{when(r.started_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  )
}

/** Bugs still open across runs: the ones a person has to fix or explicitly mark intended. */
function NeedsDecision() {
  const bugs = useApi(() => api.bugs('open'), [])
  const items = bugs.data ?? []
  return (
    <Card title="Needs a decision" hint={bugs.data ? count(items.length, 'open bug') : undefined} action={<a className="btn small ghost" href={href('findings', { view: 'bugs', status: 'open' })}>All bugs</a>} flush>
      {bugs.loading && !bugs.data ? <Loading label="Loading bugs" /> : bugs.error && !bugs.data ? <ErrorState error={bugs.error} onRetry={bugs.reload} /> : !items.length ? (
        <Empty title="No open bugs">Nothing found so far is waiting for a fix or a decision.</Empty>
      ) : (
        <ul className="plain" style={{ padding: '8px 20px 14px' }}>
          {items.slice(0, 5).map((b) => (
            <li key={b.id} className="row" style={{ gap: 10, flexWrap: 'nowrap' }}>
              <BugBadge status={b.status} />
              <span style={{ minWidth: 0 }}>
                <a href={href(`bugs/${b.id}`)}>{b.id}</a> <span className="faint small mono">{b.flow_id} × {b.attack_id}</span>
              </span>
            </li>
          ))}
        </ul>
      )}
    </Card>
  )
}

function RecentFindings() {
  const findings = useApi(() => api.findings('fail'), [])
  const items = findings.data ?? []
  return (
    <Card title="Confirmed findings" action={<a className="btn small ghost" href={href('findings', { verdict: 'fail' })}>All</a>} flush>
      {findings.loading && !findings.data ? <Loading label="Loading findings" /> : findings.error && !findings.data ? <ErrorState error={findings.error} onRetry={findings.reload} /> : !items.length ? (
        <Empty title="None confirmed yet">A run with none has not shown the app is safe; see each run's report for what was tested.</Empty>
      ) : (
        <ul className="plain" style={{ padding: '8px 20px 14px' }}>
          {items.slice(0, 4).map((f) => (
            <li key={f.id} className="row" style={{ gap: 10, flexWrap: 'nowrap', alignItems: 'flex-start' }}>
              <VerdictBadge verdict={f.verdict} />
              <span style={{ minWidth: 0 }}>
                <a href={href(`findings/${f.id}`)}>{f.title || 'Untitled finding'}</a>
                <span className="faint xs" style={{ display: 'block' }}>Run {f.run_id}</span>
              </span>
            </li>
          ))}
        </ul>
      )}
    </Card>
  )
}
