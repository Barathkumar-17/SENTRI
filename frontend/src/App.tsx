import { FlaskConical, House, ListChecks, Plus, ScrollText } from 'lucide-react'
import { api, useApi } from './api'
import { Findings } from './pages/Findings'
import { Home } from './pages/Home'
import { Ledger } from './pages/Ledger'
import { NewRun } from './pages/NewRun'
import { RunDetail } from './pages/RunDetail'
import { Runs } from './pages/Runs'
import { Banner, useRoute } from './ui'

const NAV = [
  { id: 'home', label: 'Home', icon: House },
  { id: 'runs', label: 'Runs', icon: ListChecks },
  { id: 'findings', label: 'Findings', icon: FlaskConical },
  { id: 'ledger', label: 'Ledger', icon: ScrollText },
  { id: 'new', label: 'New run', icon: Plus },
]

function Mark() {
  return (
    <svg width="22" height="22" viewBox="0 0 32 32" aria-hidden="true">
      <rect width="32" height="32" rx="8" fill="#242424" stroke="#393939" />
      <path d="M21.5 10.5c-1.2-1.4-3.1-2.2-5.4-2.2-3.4 0-5.8 1.8-5.8 4.4 0 5.4 11.2 3.4 11.2 8.6 0 2.7-2.6 4.4-6 4.4-2.6 0-4.7-1-5.9-2.6" fill="none" stroke="#fafafa" strokeWidth="2.4" strokeLinecap="round" />
    </svg>
  )
}

export default function App() {
  const route = useRoute()
  const [section, idPart] = route
  const id = idPart ? Number(idPart) : undefined
  // A light poll of the job list doubles as the server-connection check for every page.
  const jobs = useApi(api.jobs, [], 10000)
  const active = jobs.data?.find((j) => j.status === 'running' || j.status === 'queued')
  const current = section === 'runs' && id ? 'runs' : section
  return (
    <div className="shell">
      <nav className="topbar" aria-label="Main">
        <a className="brand" href="#/home"><Mark /> Sentri</a>
        {NAV.map(({ id: nid, label, icon: Icon }) => (
          <a key={nid} className="nav-link" href={`#/${nid}`} aria-current={current === nid ? 'page' : undefined}>
            <Icon size={17} aria-hidden="true" /> {label}
          </a>
        ))}
        <div className="topbar-status">
          {jobs.error ? <span><span className="dot off" aria-hidden="true" /> server unreachable</span> : <span><span className="dot on" aria-hidden="true" /> local server</span>}
          {active && <a href={active.run_id ? `#/runs/${active.run_id}` : '#/new'}>{active.kind} in progress</a>}
        </div>
      </nav>
      <main className="main" id="main">
        {jobs.error && jobs.error.status === 0 && (
          <div className="page-banner">
            <Banner tone="bad" title="Disconnected from the Sentri server">Start it again with <code>sentri ui</code>; this page reconnects on its own.</Banner>
          </div>
        )}
        {section === 'runs' && id ? <RunDetail id={id} /> : null}
        {section === 'runs' && !id ? <Runs /> : null}
        {section === 'findings' ? <Findings id={id} /> : null}
        {section === 'ledger' ? <Ledger /> : null}
        {section === 'new' ? <NewRun /> : null}
        {!['runs', 'findings', 'ledger', 'new'].includes(section) ? <Home /> : null}
      </main>
    </div>
  )
}
