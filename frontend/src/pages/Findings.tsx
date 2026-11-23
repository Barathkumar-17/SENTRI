import { useEffect, useMemo } from 'react'
import { api, useApi } from '../api'
import {
  BugBadge, Card, Empty, ErrorState, Loading, ORIGIN, PageHead, VerdictBadge, count, go, href, num, rememberList, short,
} from '../ui'

const VERDICTS = [
  { id: '', label: 'All' },
  { id: 'fail', label: 'Confirmed' },
  { id: 'flaky', label: 'Flaky' },
  { id: 'unconfirmed', label: 'Not reproduced' },
  { id: 'pass', label: 'Fixed' },
]
const STATUSES = [
  { id: '', label: 'All' },
  { id: 'open', label: 'Open' },
  { id: 'flaky', label: 'Flaky' },
  { id: 'fixed', label: 'Fixed' },
  { id: 'intended', label: 'Intended' },
]

export function Findings({ query }: { query: URLSearchParams }) {
  const view = query.get('view') === 'bugs' ? 'bugs' : 'findings'
  // the list a person is looking at, so a detail page's breadcrumb can come back to it
  useEffect(() => rememberList(window.location.hash || '#/findings'), [query])
  return (
    <>
      <PageHead
        title="Findings"
        purpose={
          view === 'bugs'
            ? 'Every bug Sentri tracks across runs and commits: when it opened, whether it was fixed or came back, and any human decision.'
            : 'Breaks the tester flagged, each replayed three times and judged by plain code. Open one for its evidence and to decide what it is.'
        }
        action={
          <nav className="segmented" aria-label="Findings view">
            <a href={href('findings')} aria-current={view === 'findings' ? 'true' : undefined}>Findings</a>
            <a href={href('findings', { view: 'bugs' })} aria-current={view === 'bugs' ? 'true' : undefined}>Bugs across runs</a>
          </nav>
        }
      />
      <div className="page-body">{view === 'bugs' ? <BugList query={query} /> : <FindingList query={query} />}</div>
    </>
  )
}

function FindingList({ query }: { query: URLSearchParams }) {
  const verdict = query.get('verdict') ?? ''
  const runFilter = query.get('run') ?? ''
  const origin = query.get('origin') ?? ''
  const list = useApi(() => api.findings(verdict || undefined), [verdict])
  const all = useMemo(() => list.data ?? [], [list.data])
  const runs = useMemo(() => [...new Set(all.map((f) => f.run_id))].sort((a, b) => b - a), [all])
  const origins = useMemo(() => [...new Set(all.map((f) => f.origin ?? ''))].filter(Boolean), [all])
  const shown = all.filter((f) => (!runFilter || String(f.run_id) === runFilter) && (!origin || f.origin === origin))
  // read the live URL, not this render's values, so two quick changes never drop one another
  const set = (k: string, v: string) => {
    const now = new URLSearchParams(window.location.hash.split('?')[1] ?? '')
    go(href('findings', { verdict: now.get('verdict'), run: now.get('run'), origin: now.get('origin'), [k]: v }))
  }
  const filtered = Boolean(verdict || runFilter || origin)
  return (
    <>
      <div className="toolbar">
        <nav className="segmented" aria-label="Filter by result">
          {VERDICTS.map((v) => (
            <a key={v.id || 'all'} href={href('findings', { verdict: v.id, run: runFilter, origin })} aria-current={verdict === v.id ? 'true' : undefined}>{v.label}</a>
          ))}
        </nav>
        <label htmlFor="f-run">Run</label>
        <select id="f-run" className="select" style={{ width: 'auto' }} value={runFilter} onChange={(e) => set('run', e.target.value)}>
          <option value="">All runs</option>
          {runs.map((r) => <option key={r} value={r}>Run {r}</option>)}
        </select>
        <label htmlFor="f-origin">Found by</label>
        <select id="f-origin" className="select" style={{ width: 'auto' }} value={origin} onChange={(e) => set('origin', e.target.value)}>
          <option value="">Anyone</option>
          {origins.map((o) => <option key={o} value={o}>{ORIGIN[o] ?? o}</option>)}
        </select>
        {filtered && <a className="btn small ghost" href={href('findings')}>Clear filters</a>}
      </div>
      <Card flush foot={list.data ? `${count(shown.length, 'finding')} shown${shown.length !== all.length ? ` of ${all.length}${verdict ? ' with this result' : ''}` : ''}` : undefined}>
        {list.loading && !list.data ? (
          <Loading label="Loading findings" />
        ) : list.error && !list.data ? (
          <ErrorState error={list.error} onRetry={list.reload} />
        ) : !shown.length ? (
          <Empty title={filtered ? 'No findings match these filters' : 'No findings yet'} action={filtered ? <a className="btn small" href={href('findings')}>Clear filters</a> : undefined}>
            {filtered ? 'Try another result, run or source.' : 'Findings appear after a run with a model flags a candidate. A run with none has not shown the app is safe.'}
          </Empty>
        ) : (
          <div className="table-wrap">
            <table>
              <thead><tr><th>Finding</th><th>Result</th><th>What fired</th><th>Found by</th><th>Run</th></tr></thead>
              <tbody>
                {shown.map((f) => (
                  <tr key={f.id} className="clickable" onClick={() => go(href(`findings/${f.id}`))}>
                    <td><a href={href(`findings/${f.id}`)} onClick={(e) => e.stopPropagation()}>{f.title || 'Untitled finding'}</a>{f.key && <span className="sub mono">{f.key}</span>}</td>
                    <td><VerdictBadge verdict={f.verdict} /></td>
                    <td className="mono xs">{f.oracle_ids || '–'}</td>
                    <td className="small">{ORIGIN[f.origin ?? ''] ?? f.origin ?? '–'}</td>
                    <td className="small"><a href={href(`runs/${f.run_id}/report`)} onClick={(e) => e.stopPropagation()}>Run {f.run_id}</a><span className="sub mono">{short(f.commit_sha)}</span></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </>
  )
}

function BugList({ query }: { query: URLSearchParams }) {
  const status = query.get('status') ?? ''
  const bugs = useApi(() => api.bugs(status || undefined), [status])
  const items = bugs.data ?? []
  return (
    <>
      <div className="toolbar">
        <nav className="segmented" aria-label="Filter by status">
          {STATUSES.map((s) => (
            <a key={s.id || 'all'} href={href('findings', { view: 'bugs', status: s.id })} aria-current={status === s.id ? 'true' : undefined}>{s.label}</a>
          ))}
        </nav>
      </div>
      <Card flush foot={bugs.data ? `${count(items.length, 'bug')}. Sentri opens, fixes and reopens bugs from what it measures; only a person marks one intended.` : undefined}>
        {bugs.loading && !bugs.data ? (
          <Loading label="Loading bugs" />
        ) : bugs.error && !bugs.data ? (
          <ErrorState error={bugs.error} onRetry={bugs.reload} />
        ) : !items.length ? (
          <Empty title={status ? 'No bugs with this status' : 'No bugs tracked'}>{status ? 'Try another status.' : 'Bugs are opened when a baseline check or a finding is confirmed.'}</Empty>
        ) : (
          <div className="table-wrap">
            <table>
              <thead><tr><th>Bug</th><th>Status</th><th>Where</th><th>What was seen</th><th className="num">Came back</th><th>First / last seen</th></tr></thead>
              <tbody>
                {items.map((b) => (
                  <tr key={b.id} className="clickable" onClick={() => go(href(`bugs/${b.id}`))}>
                    <td><a className="mono" href={href(`bugs/${b.id}`)} onClick={(e) => e.stopPropagation()}>{b.id}</a></td>
                    <td><BugBadge status={b.status} />{b.needs_review ? <span className="sub">needs review</span> : null}</td>
                    <td className="mono xs">{b.flow_id} × {b.attack_id}<span className="sub">{b.oracle_id}</span></td>
                    <td className="small muted wrap-anywhere" style={{ maxWidth: 380 }}>{b.sample_detail ?? '–'}</td>
                    <td className="num">{b.regressions ? `${num(b.regressions)}×` : '–'}</td>
                    <td className="mono xs">{short(b.first_commit)} → {short(b.last_seen_commit)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </>
  )
}
