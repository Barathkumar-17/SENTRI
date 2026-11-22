import { Fragment, useState } from 'react'
import { api, useApi } from '../api'
import { BUG_STATUS, Badge, Empty, ErrorState, Loading, PageHead, Panel, Readiness, READINESS_NOTE, Tabs, cost, go, num, short, when } from '../ui'
import { BugDecision } from './Findings'

type Tab = 'runs' | 'bugs'

export function Ledger() {
  const [tab, setTab] = useState<Tab>('runs')
  const runs = useApi(api.runs, [])
  const bugs = useApi(() => api.bugs(), [])
  return (
    <>
      <PageHead title="Ledger" context={<span>How the app fared across runs and commits, and every bug Sentri is tracking.</span>} />
      <div className="page-body">
        <Tabs<Tab> value={tab} onChange={setTab} tabs={[{ id: 'runs', label: 'Runs compared', count: runs.data?.length }, { id: 'bugs', label: 'Bugs', count: bugs.data?.length }]} />
        {tab === 'runs' ? (
          <Panel title="Runs compared" hint="impact, coverage and model use side by side; no score where nothing applicable was tested" flush>
            {runs.loading && !runs.data ? <Loading label="Loading runs" /> : runs.error && !runs.data ? <ErrorState error={runs.error} onRetry={runs.reload} /> : !runs.data?.length ? (
              <Empty title="Nothing to compare yet">Comparisons appear once there is more than one run.</Empty>
            ) : (
              <div className="table-wrap">
                <table className="nowrap">
                  <thead>
                    <tr>
                      <th>Run</th><th>Commit</th><th aria-describedby="ledger-readiness-note">Readiness</th>
                      <th className="num">Regressions</th><th className="num">Checks</th><th className="num">Steps / states</th>
                      <th className="num">Confirmed</th><th className="num wrap-cell">Model use</th>
                    </tr>
                  </thead>
                  <tbody>
                    {runs.data.map((r) => {
                      const b = r.breakdown
                      const ag = r.coverage?.agentic
                      const checks = r.coverage?.baseline?.checks
                      return (
                        <tr key={r.id} className="clickable" onClick={() => go(`/runs/${r.id}`)}>
                          <td><a href={`#/runs/${r.id}`} onClick={(e) => e.stopPropagation()}>Run {r.id}</a><span className="sub">{when(r.started_at)}</span></td>
                          <td className="mono">{short(r.commit_sha)}<span className="sub">{r.branch ?? 'local'}</span></td>
                          <td>
                            <Readiness breakdown={b} />
                            <span className="sub">baseline {b?.baseline ?? '–'} · agentic {b?.agentic_impact ? `−${b.agentic_impact}` : '0'}</span>
                          </td>
                          <td className="num">{num(b?.regressed)}</td>
                          <td className="num">{checks ? `${num(checks.passed)} pass · ${num(checks.failed)} fail` : `${num(b?.passed)} pass · ${num(b?.failed)} fail`}<span className="sub">{checks ? `${num(checks.inapplicable)} n/a · ${num(checks.inconclusive)} inconclusive` : ''}</span></td>
                          <td className="num">{ag ? `${num(ag.steps)} / ${num(ag.distinct_states)}` : '–'}</td>
                          <td className="num">{ag ? `${num(ag.confirmed)} of ${num(ag.candidates)}` : '–'}</td>
                          <td className="num wrap-cell">
                            {num(r.usage?.calls)} calls
                            <span className="sub">{r.usage?.calls ? `${num(r.usage.input_tokens)} in / ${num(r.usage.output_tokens)} out · ${cost(r.usage.cost_usd, r.usage.calls)}` : 'no model calls'}</span>
                          </td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>
            )}
            {!!runs.data?.length && <p id="ledger-readiness-note" className="footnote panel-foot">{READINESS_NOTE}</p>}
          </Panel>
        ) : (
          <BugList bugs={bugs} />
        )}
      </div>
    </>
  )
}

function BugList({ bugs }: { bugs: ReturnType<typeof useApi<import('../api').Bug[]>> }) {
  const [open, setOpen] = useState<string | null>(null)
  if (bugs.loading && !bugs.data) return <Panel><Loading label="Loading bugs" /></Panel>
  if (bugs.error && !bugs.data) return <Panel><ErrorState error={bugs.error} onRetry={bugs.reload} /></Panel>
  if (!bugs.data?.length) return <Panel><Empty title="No bugs tracked">Bugs are opened when a check or finding is confirmed. None so far.</Empty></Panel>
  return (
    <Panel title="Bugs" hint="open, flaky, fixed and intended; regressions counted across commits" flush>
      <div className="table-wrap">
        <table>
          <thead><tr><th>Bug</th><th>Status</th><th>Where</th><th className="hide-narrow">Detail</th><th className="num">Regressions</th><th className="hide-narrow">Last seen</th></tr></thead>
          <tbody>
            {bugs.data.map((b) => {
              const [tone, label] = BUG_STATUS[b.status] ?? ['', b.status]
              return (
                <Fragment key={b.id}>
                  <tr className="clickable" onClick={() => setOpen(open === b.id ? null : b.id)}>
                    <td>
                      <button type="button" className="link-btn mono" aria-expanded={open === b.id} onClick={(e) => { e.stopPropagation(); setOpen(open === b.id ? null : b.id) }}>
                        {b.id}
                      </button>
                    </td>
                    <td><Badge tone={tone}>{label}</Badge>{b.needs_review ? <span className="sub">needs review</span> : null}</td>
                    <td className="mono xs">{b.flow_id} × {b.attack_id}<span className="sub">{b.oracle_id}</span></td>
                    <td className="hide-narrow small muted wrap-anywhere">{b.sample_detail ?? '–'}</td>
                    <td className="num">{num(b.regressions)}</td>
                    <td className="hide-narrow mono">{short(b.last_seen_commit)}</td>
                  </tr>
                  {open === b.id && (
                    <tr><td colSpan={6}><BugDecision bug={b} onChanged={bugs.reload} /></td></tr>
                  )}
                </Fragment>
              )
            })}
          </tbody>
        </table>
      </div>
    </Panel>
  )
}
