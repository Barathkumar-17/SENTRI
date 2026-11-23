import { Card, PageHead } from '../ui'

// Verified against README.md, documentation/contract.md, models.md and cli.md. Commands only; nothing here runs.
function Cmd({ children }: { children: string }) {
  return <pre className="code nowrap">{children}</pre>
}

export function GettingStarted({ embedded = false }: { embedded?: boolean }) {
  const body = (
    <div className="stack" style={{ gap: 20 }}>
      <Card title="Two programs, one machine" hint="what talks to what">
        <ul className="bullets">
          <li>
            <strong className="muted">Sentri</strong> is the tester: a command-line tool plus this local web app. It drives a real browser and HTTP
            requests against your app, and keeps its results in a ledger on this machine.
          </li>
          <li>
            <strong className="muted">The target</strong> is your web app. Sentri either starts it for you (from a command or a Dockerfile) or connects
            to one that is already running at a URL. Sentri tests it from the outside only: never its source, README, database or logs.
          </li>
        </ul>
      </Card>

      <Card title="Set up and launch">
        <ol className="guide-steps">
          <li>
            <div>
              <h3>Install Sentri <span className="who">once</span></h3>
              <p>Needs Python 3.12 or newer on Windows, macOS or Linux. <code>sentri setup</code> downloads the Chromium build the tester drives; <code>sentri doctor</code> checks Python, the browser, Docker, git and your model settings.</p>
              <Cmd>{'pip install git+https://github.com/Barathkumar-17/SENTRI.git\nsentri setup\nsentri doctor'}</Cmd>
            </div>
          </li>
          <li>
            <div>
              <h3>Open this web app <span className="who">each session</span></h3>
              <p>
                <code>sentri ui</code> starts the local server at <code>http://127.0.0.1:8787</code> and opens it; <code>sentri serve</code> does the same
                without a browser. It answers only on this machine.
              </p>
              <Cmd>sentri ui</Cmd>
            </div>
          </li>
          <li>
            <div>
              <h3>Decide how Sentri reaches your app</h3>
              <p>The contract's <code>app:</code> section says which of three ways applies:</p>
              <ul className="bullets small">
                <li><strong className="muted">Command</strong>: Sentri runs your start command (for example <code>run: "npm start"</code> with <code>port: 3000</code>) and restarts the app before every replay, so each attempt starts clean.</li>
                <li><strong className="muted">URL</strong>: give only <code>base_url</code> for an app that is already running. Sentri never starts, resets or stops it, so replays run on its live state.</li>
                <li><strong className="muted">Docker</strong>: <code>docker: {'{context: ".", dockerfile: "Dockerfile"}'}</code>; needs Docker installed. Supported by the engine, but not verified on this release's test machine.</li>
              </ul>
            </div>
          </li>
          <li>
            <div>
              <h3>Keep credentials out of files</h3>
              <ul className="bullets small">
                <li>Test accounts: placeholders such as <code>{'{email}'}</code> and <code>{'{password}'}</code> are generated fresh for each attempt. For an app that needs an existing account, set <code>SENTRI_SEED_EMAIL</code> and <code>SENTRI_SEED_PASSWORD</code> in the environment and use <code>{'{seed_email}'}</code> / <code>{'{seed_password}'}</code>.</li>
                <li>Model keys: set <code>SENTRI_LLM_API_KEY</code> (or <code>ANTHROPIC_API_KEY</code> for Anthropic) before <code>sentri ui</code>, or type a key in New run, where it stays in memory for the session. A signed-in Claude Code, Codex or Gemini CLI, or a local model, needs no key. No model at all runs the six fixed baseline attacks only.</li>
                <li>Keys and provider account ids are scrubbed from everything Sentri stores or shows. Never put a key in a contract.</li>
              </ul>
            </div>
          </li>
          <li>
            <div>
              <h3>Start testing</h3>
              <p>
                In <a href="#/new">New run</a>: choose a model, point at your app folder or URL, draft or paste the contract, validate it and approve it
                under your name, then start. Follow the run live in <a href="#/runs">Runs</a>, then open its report.
              </p>
              <p className="small">The same from the command line:</p>
              <Cmd>{'sentri plan --app-dir . --run "npm start" --port 3000\nsentri contract approve contract.draft.yaml --as "you@example.com"\nsentri run --contract contract.yaml --llm-provider auto --out report.md'}</Cmd>
            </div>
          </li>
        </ol>
      </Card>

      <Card title="Try it on the demo app first" hint="planted bugs, nothing of yours at risk">
        <p className="muted small" style={{ marginBottom: 12 }}>From a clone of the Sentri repository:</p>
        <Cmd>{'pip install -e ".[dev]" && sentri setup\nsentri contract approve examples/buggy-app/contract.yaml --as "<your name>" --out examples/buggy-app/approved.yaml\nsentri run --contract examples/buggy-app/approved.yaml --out report.md'}</Cmd>
        <p className="footnote" style={{ marginTop: 12 }}>Only test apps you own or are allowed to test: Sentri behaves like a hostile user on purpose.</p>
      </Card>
    </div>
  )
  if (embedded) return body
  return (
    <>
      <PageHead
        crumbs={[{ label: 'Home', href: '#/home' }, { label: 'Getting started' }]}
        title="Getting started"
        purpose="Install Sentri, connect it to the app you want to test, keep credentials safe, and start your first run."
      />
      <div className="page-body">{body}</div>
    </>
  )
}
