import { useEffect } from 'react'
import { BookOpen, FlaskConical, House, ListChecks, Plus } from 'lucide-react'
import { api, useApi } from './api'
import { BugPage } from './pages/BugPage'
import { FindingPage } from './pages/FindingPage'
import { Findings } from './pages/Findings'
import { GettingStarted } from './pages/GettingStarted'
import { Home } from './pages/Home'
import { NewRun } from './pages/NewRun'
import { Report } from './pages/Report'
import { RunDetail } from './pages/RunDetail'
import { Runs } from './pages/Runs'
import { Banner, go, useRoute } from './ui'

const NAV = [
  { id: 'home', label: 'Home', icon: House },
  { id: 'runs', label: 'Runs', icon: ListChecks },
  { id: 'findings', label: 'Findings', icon: FlaskConical },
  { id: 'new', label: 'New run', icon: Plus },
]

function Mark() {
  return (
    <svg width="22" height="22" viewBox="0 0 32 32" aria-hidden="true">
      <rect width="32" height="32" rx="7" fill="#1a1a1a" stroke="#3d3d3d" />
      <path d="M21.5 10.5c-1.2-1.4-3.1-2.2-5.4-2.2-3.4 0-5.8 1.8-5.8 4.4 0 5.4 11.2 3.4 11.2 8.6 0 2.7-2.6 4.4-6 4.4-2.6 0-4.7-1-5.9-2.6" fill="none" stroke="#3ecf8e" strokeWidth="2.4" strokeLinecap="round" />
    </svg>
  )
}

const TITLES: Record<string, string> = { home: 'Home', runs: 'Runs', findings: 'Findings', bugs: 'Bug', new: 'New run', start: 'Getting started' }

export default function App() {
  const { parts, query } = useRoute()
  const [section, idPart, sub] = parts
  const id = idPart && /^\d+$/.test(idPart) ? Number(idPart) : undefined
  // A light poll of the job list doubles as the server-connection check for every page.
  const jobs = useApi(api.jobs, [], 10000)
  const active = jobs.data?.find((j) => j.status === 'running' || j.status === 'queued')
  const current = section === 'bugs' ? 'findings' : section === 'start' ? '' : section

  useEffect(() => {
    if (section === 'ledger') go('/findings?view=bugs') // the old Ledger screen now lives in Findings
  }, [section])
  useEffect(() => {
    document.title = `${TITLES[section] ?? 'Sentri'} · Sentri`
  }, [section])

  let page
  if (section === 'runs' && id && sub === 'report') page = <Report id={id} />
  else if (section === 'runs' && id) page = <RunDetail id={id} />
  else if (section === 'runs') page = <Runs />
  else if (section === 'findings' && id) page = <FindingPage id={id} />
  else if (section === 'findings') page = <Findings query={query} />
  else if (section === 'bugs' && idPart) page = <BugPage id={idPart} />
  else if (section === 'new') page = <NewRun />
  else if (section === 'start') page = <GettingStarted />
  else page = <Home />

  return (
    <div className="shell">
      <a className="sr-only" href="#main">Skip to content</a>
      <nav className="topbar" aria-label="Main">
        <a className="brand" href="#/home"><Mark /> Sentri</a>
        {NAV.map(({ id: nid, label, icon: Icon }) => (
          <a key={nid} className="nav-link" href={`#/${nid}`} aria-current={current === nid ? 'page' : undefined}>
            <Icon size={16} aria-hidden="true" /> {label}
          </a>
        ))}
        <div className="topbar-end">
          {active && <a href={active.run_id ? `#/runs/${active.run_id}` : '#/new'}>{active.kind} in progress</a>}
          <a href="#/start" aria-current={section === 'start' ? 'page' : undefined}>
            <span className="row" style={{ gap: 6 }}><BookOpen size={14} aria-hidden="true" /> Getting started</span>
          </a>
          {jobs.error ? <span><span className="dot off" aria-hidden="true" /> server unreachable</span> : <span><span className="dot on" aria-hidden="true" /> local server</span>}
        </div>
      </nav>
      <main className="main" id="main" tabIndex={-1}>
        {jobs.error && jobs.error.status === 0 && (
          <div className="page-banner">
            <Banner tone="bad" title="Disconnected from the Sentri server">Start it again with <code>sentri ui</code>; this page reconnects on its own.</Banner>
          </div>
        )}
        {page}
      </main>
    </div>
  )
}
