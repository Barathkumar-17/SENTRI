import { Plus } from 'lucide-react'
import { api, useApi } from '../api'
import { PageHead, Panel, Readiness, RunStatus, when } from '../ui'

// How to use Sentri from this app. Every step names the screen that does it; nothing here is sample data.
const STEPS = [
  {
    title: 'Choose a model, or none',
    body: 'With no model Sentri runs its fixed baseline attacks with built-in payloads. With a model it also plans an attack strategy for your app and explores it step by step.',
    href: '#/new',
    link: 'New run, step 1',
  },
  {
    title: 'Point Sentri at your app',
    body: 'Give the project folder so Sentri can start and restart the app itself, or a URL for an app that is already running. Sentri only reaches the app through its pages and HTTP.',
    href: '#/new',
    link: 'New run, step 2',
  },
  {
    title: 'Draft, check and approve the contract',
    body: 'The contract says which flows exist, which users Sentri may log in as and what it may attack. Draft one or paste your own, validate it, then approve it under your name.',
    href: '#/new',
    link: 'New run, step 2',
  },
  {
    title: 'Start the run and follow it',
    body: 'One run at a time. The activity log updates live; the run keeps going if you leave the page. If a model quota or budget stops it early, the run says so.',
    href: '#/runs',
    link: 'Runs',
  },
  {
    title: 'Read what broke',
    body: 'Each finding was replayed three times (from a restarted app when Sentri starts it) and plain code decided the verdict. Open one to see what fired, every attempt’s responses and the steps replayed.',
    href: '#/findings',
    link: 'Findings',
  },
  {
    title: 'Compare runs and decide',
    body: 'The ledger lines runs up across commits and keeps every bug. Only a person can mark a bug intended, and it needs a reason.',
    href: '#/ledger',
    link: 'Ledger',
  },
]

export function Home() {
  const runs = useApi(api.runs, [])
  const latest = runs.data?.[0]
  return (
    <>
      <PageHead
        title="Sentri"
        context={<span>Attacks your running web app like a hostile or careless user, so it breaks here instead of in a demo.</span>}
        action={
          <a className="btn primary" href="#/new">
            <Plus size={16} aria-hidden="true" /> New run
          </a>
        }
      />
      <div className="page-body home">
        <Panel title="How to use Sentri" hint="six steps, each on its own screen">
          <ol className="guide">
            {STEPS.map((s, i) => (
              <li key={s.title}>
                <span className="n" aria-hidden="true">{i + 1}</span>
                <div>
                  <h3>{s.title}</h3>
                  <p>{s.body}</p>
                  <a href={s.href}>{s.link}</a>
                </div>
              </li>
            ))}
          </ol>
        </Panel>

        <div className="stack">
          <Panel title="Black box, always">
            <ul className="plain small muted">
              <li>The tester learns your app only from its running pages and HTTP responses, using the users the contract allows.</li>
              <li>Source code, README files, database internals and server logs never reach the model, its memory or a verdict.</li>
              <li>A project folder is used only to start, stop and restart the app.</li>
            </ul>
          </Panel>

          <Panel title="Reading a result">
            <dl className="kv">
              <dt>Readiness</dt>
              <dd>Out of 100: the share of applicable baseline checks that passed, minus confirmed findings. Not a safety rating.</dd>
              <dt>Insufficient coverage</dt>
              <dd>Nothing applicable was tested, so there is no score. It is never a pass.</dd>
              <dt>Confirmed</dt>
              <dd>The break was seen in every replay. Flaky: in some.</dd>
            </dl>
          </Panel>

          <Panel title="This machine">
            {runs.error ? (
              <p className="small muted">The run list could not be loaded.</p>
            ) : !runs.data ? (
              <p className="small muted">Loading…</p>
            ) : !latest ? (
              <p className="small muted">No runs yet. Start with New run.</p>
            ) : (
              <div className="stack" style={{ gap: 8 }}>
                <div className="row">
                  <a href={`#/runs/${latest.id}`}>Latest: Run {latest.id}</a>
                  <RunStatus run={latest} />
                  <Readiness breakdown={latest.breakdown} />
                </div>
                <span className="small muted">
                  {when(latest.started_at)} · {runs.data.length} {runs.data.length === 1 ? 'run' : 'runs'} in this ledger
                </span>
              </div>
            )}
          </Panel>
        </div>
      </div>
    </>
  )
}
