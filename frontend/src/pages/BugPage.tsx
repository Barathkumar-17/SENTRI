import { ApiError, api, useApi, type Bug } from '../api'
import { BugBadge, Card, Empty, ErrorState, Loading, PageHead, href, short, when } from '../ui'
import { BugDecision } from './BugDecision'

// A bug's life across runs and commits: what it is, every status change and who made it, and the decision.
export function BugPage({ id }: { id: string }) {
  const bugs = useApi(() => api.bugs(), [id])
  const history = useApi(() => api.bugHistory(id), [id])
  const crumbs = [{ label: 'Findings', href: href('findings') }, { label: 'Bugs across runs', href: href('findings', { view: 'bugs' }) }, { label: id }]
  const reload = () => {
    bugs.reload()
    history.reload()
  }
  if (bugs.loading && !bugs.data) return <Shell crumbs={crumbs} id={id}><Card><Loading label="Loading bug" /></Card></Shell>
  if (bugs.error && !bugs.data) return <Shell crumbs={crumbs} id={id}><Card><ErrorState error={bugs.error} onRetry={reload} what="bug" /></Card></Shell>
  const bug = bugs.data?.find((b) => b.id === id)
  if (!bug) return <Shell crumbs={crumbs} id={id}><Card><ErrorState error={new ApiError(404, 'bug not found')} what="bug" /></Card></Shell>
  return (
    <>
      <PageHead
        crumbs={crumbs}
        title={<span className="row" style={{ gap: 12 }}><span className="mono">{bug.id}</span><BugBadge status={bug.status} /></span>}
        purpose={bug.sample_detail ?? undefined}
        meta={<><span className="mono">{bug.flow_id} × {bug.attack_id}</span><span className="mono">{bug.oracle_id}</span></>}
      />
      <div className="page-body">
        <div className="grid-main">
          <div className="stack" style={{ gap: 20 }}>
            <Card title="Across runs" hint="every status change, oldest first" flush>
              {history.loading && !history.data ? <Loading label="Loading history" /> : history.error && !history.data ? <ErrorState error={history.error} onRetry={history.reload} /> : !history.data?.length ? (
                <Empty title="No history recorded" />
              ) : (
                <ol className="timeline" style={{ padding: '4px 20px' }}>
                  {history.data.map((h) => (
                    <li key={h.id}>
                      <span className="when">{when(h.ts)}</span>
                      <span>
                        <span className="row" style={{ gap: 8 }}>
                          {h.from_status ? <><BugBadge status={h.from_status} /> → </> : 'Opened as '}
                          <BugBadge status={h.to_status} />
                          <span className="faint small">{h.actor === 'human' ? 'by a person' : 'by Sentri'}</span>
                        </span>
                        <span className="faint xs" style={{ display: 'block', marginTop: 4 }}>
                          {h.run_id ? <a href={href(`runs/${h.run_id}/report`)}>Run {h.run_id}</a> : 'no run'} · <span className="mono">{short(h.commit_sha)}</span>
                          {h.note ? ` · ${h.note}` : ''}
                        </span>
                      </span>
                    </li>
                  ))}
                </ol>
              )}
            </Card>
            <Card title="Decision" hint="a person decides; Sentri never marks a bug intended" className="decision">
              <BugDecision bug={bug} onChanged={reload} />
            </Card>
          </div>
          <Facts bug={bug} />
        </div>
      </div>
    </>
  )
}

function Facts({ bug }: { bug: Bug }) {
  return (
    <Card title="Record">
      <dl className="kv">
        <dt>Where</dt><dd className="mono">{bug.flow_id} × {bug.attack_id}</dd>
        <dt>Check that fired</dt><dd className="mono">{bug.oracle_id}</dd>
        <dt>First seen</dt><dd className="mono">{short(bug.first_commit)}</dd>
        <dt>Last seen</dt><dd className="mono">{short(bug.last_seen_commit)}</dd>
        <dt>Fixed in</dt><dd className="mono">{bug.fixed_commit ? short(bug.fixed_commit) : '–'}</dd>
        <dt>Came back</dt><dd>{bug.regressions ? `${bug.regressions}×` : 'never'}</dd>
        {bug.needs_review ? <><dt>Review</dt><dd>needs review</dd></> : null}
      </dl>
    </Card>
  )
}

function Shell({ id, crumbs, children }: { id: string; crumbs: { label: string; href?: string }[]; children: React.ReactNode }) {
  return (
    <>
      <PageHead crumbs={crumbs} title={<span className="mono">{id}</span>} />
      <div className="page-body">{children}</div>
    </>
  )
}
