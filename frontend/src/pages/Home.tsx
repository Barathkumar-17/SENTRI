import { Plus } from 'lucide-react'
import { api, useApi } from '../api'
import { PageHead, Panel } from '../ui'

// How to use Sentri from this app: one plain list, with links to the screen that does each step.
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
      <div className="page-body">
        <Panel title="How to use Sentri">
          <ol className="guide">
            <li>
              Open <a href="#/new">New run</a> and choose a model. With no model, only the six fixed baseline attacks run.
            </li>
            <li>Give the app's folder so Sentri can start and restart it, or the URL of an app that is already running.</li>
            <li>Draft or paste the contract (the flows, the users Sentri may log in as, what it may attack), validate it and approve it under your name.</li>
            <li>
              Start the run and follow it live in <a href="#/runs">Runs</a>. If a model quota or budget stops it early, the run says so.
            </li>
            <li>
              Read what broke in <a href="#/findings">Findings</a>. Every finding is replayed three times and plain code decides the verdict.
            </li>
            <li>
              Compare runs across commits in the <a href="#/ledger">Ledger</a>, and mark a bug intended there when it is (a reason is required).
            </li>
          </ol>
          <p className="home-note">
            Sentri tests from the outside only: the model sees your app's pages and HTTP responses, never its source, README, database or server logs.
            Readiness is out of 100 and counts only what a run tested; it is not a safety rating.
            {latest && (
              <>
                {' '}Your latest run is <a href={`#/runs/${latest.id}`}>Run {latest.id}</a>.
              </>
            )}
          </p>
        </Panel>
      </div>
    </>
  )
}
