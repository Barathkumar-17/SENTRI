import { Plus } from 'lucide-react'
import { api, useApi, type Run } from '../api'
import { Badge, count, Empty, ErrorState, Loading, PageHead, Panel, Readiness, READINESS_NOTE, RunStatus, cost, readinessBasis, duration, go, num, quotaStopped, short, when } from '../ui'

export function Runs() {
  const runs = useApi(api.runs, [], 5000)
  const running = runs.data?.some((r) => r.status === 'running')
  return (
    <>
      <PageHead
        title="Runs"
        context={<span>Every run recorded in this machine's ledger, newest first.</span>}
        action={
          <a className="btn primary" href="#/new">
            <Plus size={16} aria-hidden="true" /> New run
          </a>
        }
      />
      <div className="page-body">
        <Panel title="Recent runs" hint={running ? 'A run is in progress; this list refreshes every 5 s.' : undefined} flush>
          {runs.loading && !runs.data ? (
            <Loading label="Loading runs" />
          ) : runs.error && !runs.data ? (
            <ErrorState error={runs.error} onRetry={runs.reload} />
          ) : !runs.data?.length ? (
            <Empty title="No runs yet" action={<a className="btn primary small" href="#/new">Start the first run</a>}>
              Runs appear here once Sentri has tested an app from this machine, from here or with{' '}
              <code>sentri run</code>.
            </Empty>
          ) : (
            <RunsTable runs={runs.data} />
          )}
          {!!runs.data?.length && <p id="readiness-note" className="footnote panel-foot">{READINESS_NOTE}</p>}
        </Panel>
      </div>
    </>
  )
}

export function RunsTable({ runs }: { runs: Run[] }) {
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Run</th>
            <th className="hide-narrow">Status</th>
            <th aria-describedby="readiness-note">Readiness</th>
            <th className="num hide-narrow">Found</th>
            <th className="hide-narrow">Exploration</th>
            <th className="hide-narrow">Model use</th>
            <th className="num hide-narrow">Duration</th>
          </tr>
        </thead>
        <tbody>
          {runs.map((r) => {
            const ag = r.coverage?.agentic
            const stop = quotaStopped(r)
            return (
              <tr key={r.id} className="clickable" onClick={() => go(`/runs/${r.id}`)}>
                <td>
                  <a href={`#/runs/${r.id}`} onClick={(e) => e.stopPropagation()}>
                    Run {r.id}
                  </a>
                  <span className="sub">
                    <span className="mono">{short(r.commit_sha)}</span> · {r.branch ?? 'local'}
                  </span>
                  <span className="sub hide-narrow">{when(r.started_at)}</span>
                  <span className="sub show-narrow">
                    <RunStatus run={r} />
                    {stop && <span className="sub">{/^(stopped|model use)/.test(stop) ? stop : `stopped: ${stop}`}</span>}
                    {ag && <span className="sub">{num(ag.confirmed)} confirmed of {count(ag.candidates ?? 0, 'candidate')}</span>}
                  </span>
                </td>
                <td className="hide-narrow">
                  <RunStatus run={r} />
                  {stop && <span className="sub">{/^(stopped|model use)/.test(stop) ? stop : `stopped: ${stop}`}</span>}
                </td>
                <td>
                  <Readiness breakdown={r.breakdown} />
                  <span className="sub">{readinessBasis(r.breakdown)}</span>
                </td>
                <td className="num hide-narrow">
                  {ag ? (
                    <>
                      {num(ag.confirmed)}
                      <span className="sub">of {count(ag.candidates ?? 0, 'candidate')}</span>
                    </>
                  ) : (
                    <span className="faint">–</span>
                  )}
                </td>
                <td className="hide-narrow">
                  {ag ? (
                    <>
                      {num(ag.steps)} steps{ag.distinct_states != null && <> · {num(ag.distinct_states)} states</>}
                      <span className="sub">
                        {ag.ai_steps != null ? `${num(ag.ai_steps)} chosen by the model` : (ag.exploration_stop ?? 'no exploration record')}
                      </span>
                    </>
                  ) : (
                    <Badge tone="outline">Baseline only</Badge>
                  )}
                </td>
                <td className="hide-narrow">
                  {r.usage ? (
                    <>
                      {num(r.usage.calls)} calls
                      <span className="sub">{cost(r.usage.cost_usd, r.usage.calls)}</span>
                    </>
                  ) : (
                    <span className="faint">–</span>
                  )}
                </td>
                <td className="num hide-narrow">{duration(r.duration_s)}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}
