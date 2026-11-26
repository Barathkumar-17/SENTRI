import { Plus } from 'lucide-react'
import { api, useApi, type Run } from '../api'
import {
  Banner, Card, Empty, ErrorState, Loading, PageHead, READINESS_NOTE, Readiness, RunStatus, cost, count, duration, go, href,
  num, quotaStopped, readinessBasis, short, when,
} from '../ui'

export function Runs() {
  const runs = useApi(api.runs, [], 5000)
  const live = runs.data?.filter((r) => r.status === 'running') ?? []
  return (
    <>
      <PageHead
        title="Runs"
        purpose="Every test run on this machine, newest first. Open a run to follow it live; open its report to see what was tested and what broke."
        action={<a className="btn primary" href="#/new"><Plus size={16} aria-hidden="true" /> New run</a>}
      />
      <div className="page-body">
        {live.map((r) => (
          <Banner key={r.id} tone="info" title={<>Run {r.id} is running</>}>
            <a href={href(`runs/${r.id}`)}>Follow it live</a>. This list refreshes every 5 seconds.
          </Banner>
        ))}
        <Card flush foot={runs.data?.length ? <span id="readiness-note">{READINESS_NOTE}</span> : undefined}>
          {runs.loading && !runs.data ? (
            <Loading label="Loading runs" />
          ) : runs.error && !runs.data ? (
            <ErrorState error={runs.error} onRetry={runs.reload} />
          ) : !runs.data?.length ? (
            <Empty title="No runs yet" action={<a className="btn primary small" href="#/new">Start the first run</a>}>
              Runs appear here once Sentri has tested an app from this machine, from here or with <code>sentri run</code>. New here? Read <a href="#/start">Getting started</a>.
            </Empty>
          ) : (
            <RunsTable runs={runs.data} />
          )}
        </Card>
      </div>
    </>
  )
}

function RunsTable({ runs }: { runs: Run[] }) {
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Run</th>
            <th>Status</th>
            <th aria-describedby="readiness-note">Readiness</th>
            <th className="num">Confirmed</th>
            <th>Baseline checks</th>
            <th>Exploration</th>
            <th>Model use</th>
            <th><span className="sr-only">Report</span></th>
          </tr>
        </thead>
        <tbody>
          {runs.map((r) => {
            const ag = r.coverage?.agentic
            const checks = r.coverage?.baseline?.checks
            const stop = quotaStopped(r)
            return (
              <tr key={r.id} className="clickable" onClick={() => go(`/runs/${r.id}`)}>
                <td>
                  <a href={href(`runs/${r.id}`)} onClick={(e) => e.stopPropagation()}>Run {r.id}</a>
                  <span className="sub"><span className="mono">{short(r.commit_sha)}</span> · {r.branch ?? 'local'}</span>
                  <span className="sub">{when(r.started_at)}{r.duration_s != null ? ` · ${duration(r.duration_s)}` : ''}</span>
                </td>
                <td>
                  <RunStatus run={r} />
                  {stop && <span className="sub">{/^(stopped|model use)/.test(stop) ? stop : `stopped: ${stop}`}</span>}
                </td>
                <td>
                  <Readiness breakdown={r.breakdown} />
                  <span className="sub">{readinessBasis(r.breakdown)}</span>
                </td>
                <td className="num">
                  {ag ? num(ag.confirmed) : '–'}
                  {ag && <span className="sub">of {count(ag.candidates ?? 0, 'candidate')}</span>}
                </td>
                <td>
                  {checks ? `${num(checks.passed)} pass · ${num(checks.failed)} fail${checks.flaky ? ` · ${num(checks.flaky)} flaky` : ''}` : <span className="faint">not run</span>}
                  {checks && <span className="sub">{num(checks.inapplicable)} n/a{r.breakdown?.regressed ? ` · ${count(r.breakdown.regressed, 'regression')}` : ''}</span>}
                </td>
                <td>
                  {ag ? count(ag.steps ?? 0, 'step') : <span className="faint">not run</span>}
                  {ag && <span className="sub">{ag.ai_steps != null ? `${num(ag.ai_steps)} chosen by the model` : ag.exploration_stop ?? ''}</span>}
                </td>
                <td>
                  {r.usage ? count(r.usage.calls, 'call') : '–'}
                  {r.usage && <span className="sub">{cost(r.usage.cost_usd, r.usage.calls)}</span>}
                </td>
                <td className="num">
                  <a className="btn small" href={href(`runs/${r.id}/report`)} onClick={(e) => e.stopPropagation()}>Report</a>
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}
