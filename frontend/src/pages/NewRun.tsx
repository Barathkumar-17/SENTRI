import { useEffect, useState, type ReactNode } from 'react'
import { CheckCircle2, Play } from 'lucide-react'
import { api, useApi, type Job, type ModelChoice, type Provider } from '../api'
import { Banner, Card as Panel, ErrorState, Loading, PageHead, go } from '../ui'

/** The whole local workflow: model -> app and contract -> human approval -> run. Real endpoints only. */
export function NewRun() {
  const [modelReady, setModelReady] = useState(false)
  const [contractPath, setContractPath] = useState('')
  const [approved, setApproved] = useState(false)
  return (
    <>
      <PageHead crumbs={[{ label: 'Runs', href: '#/runs' }, { label: 'New run' }]} title="New run" purpose={<>Three steps: choose the model that plans and explores, point Sentri at your app and approve what it may test, then start. New to Sentri? See <a href="#/start">Getting started</a>.</>} />
      <div className="page-body stepper">
        <Step n={1} title="Model" done={modelReady}><ModelStep onReady={setModelReady} /></Step>
        <Step n={2} title="App and contract" done={approved}>
          <ContractStep onApproved={(path) => { setContractPath(path); setApproved(true) }} />
        </Step>
        <Step n={3} title="Run" done={false}><RunStep key={contractPath} contractPath={contractPath} approved={approved} /></Step>
      </div>
    </>
  )
}

function Step({ n, title, done, children }: { n: number; title: string; done: boolean; children: ReactNode }) {
  return (
    <section className="stack" aria-labelledby={`step-${n}`}>
      <div className="step-head">
        <span className={`step-num${done ? ' done' : ''}`} aria-hidden="true">{done ? <CheckCircle2 size={14} /> : n}</span>
        <h2 id={`step-${n}`}>{title}</h2>
        {done && <span className="sr-only">(done)</span>}
      </div>
      {children}
    </section>
  )
}

function ModelStep({ onReady }: { onReady: (ok: boolean) => void }) {
  const providers = useApi(api.providers, [])
  const current = useApi(api.getModel, [])
  if ((providers.loading && !providers.data) || (current.loading && !current.data)) return <Panel><Loading label="Loading model choices" /></Panel>
  if (providers.error) return <Panel><ErrorState error={providers.error} onRetry={providers.reload} /></Panel>
  if (current.error) return <Panel><ErrorState error={current.error} onRetry={current.reload} /></Panel>
  return <ModelForm providers={providers.data ?? []} saved={current.data as ModelChoice} onReady={onReady} />
}

function ModelForm({ providers, saved, onReady }: { providers: Provider[]; saved: ModelChoice; onReady: (ok: boolean) => void }) {
  const [choice, setChoice] = useState<ModelChoice>(saved)
  const [key, setKey] = useState('')
  const [status, setStatus] = useState<{ ok: boolean; text: string } | null>(null)
  const [busy, setBusy] = useState(false)
  const preset = providers.find((p) => p.id === choice.preset)
  const save = async (test: boolean) => {
    setBusy(true)
    setStatus(null)
    const body: ModelChoice = { ...choice, api_key: key || null }
    try {
      if (test) {
        const r = await api.testModel(body)
        setStatus({ ok: r.ok, text: r.detail ?? (r.ok ? 'reachable' : 'not reachable') })
      }
      await api.putModel(body)
      onReady(true)
      if (!test) setStatus({ ok: true, text: 'Saved for this session.' })
    } catch (e) {
      setStatus({ ok: false, text: (e as Error).message })
    } finally {
      setBusy(false)
    }
  }
  return (
    <Panel title="Which model plans and explores?" hint="no model = baseline suite only, no agent">
      <div className="stack">
        <div className="form-grid" role="radiogroup" aria-label="Model provider">
          {providers.map((p) => (
            <label key={p.id} className="choice">
              <input type="radio" name="preset" checked={choice.preset === p.id} onChange={() => setChoice({ preset: p.id, base_url: p.base_url, model: null })} />
              <span><span className="title">{p.label}</span><br /><span className="desc">{p.note}</span></span>
            </label>
          ))}
        </div>
        {preset && preset.id !== 'none' && (
          <div className="form-grid">
            {preset.kind === 'openai_compatible' && (
              <div className="field">
                <label htmlFor="base-url">Base URL</label>
                <input id="base-url" className="input mono" value={choice.base_url ?? ''} onChange={(e) => setChoice({ ...choice, base_url: e.target.value })} placeholder="https://…/v1" />
              </div>
            )}
            <div className="field">
              <label htmlFor="model-id">Model</label>
              <input id="model-id" className="input mono" value={choice.model ?? ''} onChange={(e) => setChoice({ ...choice, model: e.target.value || null })} placeholder="provider default" />
            </div>
            {preset.needs_key && (
              <div className="field">
                <label htmlFor="api-key">API key</label>
                <input id="api-key" className="input mono" type="password" autoComplete="off" value={key} onChange={(e) => setKey(e.target.value)} placeholder={choice.has_key ? 'a key is set for this session' : preset.key_env ? `or set ${preset.key_env}` : ''} />
                <span className="help">Kept in the server's memory for this session only; never written to disk or shown again.</span>
              </div>
            )}
          </div>
        )}
        <div className="row">
          <button className="btn small" disabled={busy} onClick={() => save(true)}>Test and use</button>
          <button className="btn ghost small" disabled={busy} onClick={() => save(false)}>Use without testing</button>
          {status && <span className="small" role="status" style={{ color: status.ok ? 'var(--ok)' : 'var(--bad)' }}>{status.text}</span>}
        </div>
      </div>
    </Panel>
  )
}

function ContractStep({ onApproved }: { onApproved: (path: string) => void }) {
  const [folder, setFolder] = useState('')
  const [inspect, setInspect] = useState<Awaited<ReturnType<typeof api.inspect>> | null>(null)
  const [mode, setMode] = useState<'run' | 'url'>('run')
  const [runCmd, setRunCmd] = useState('')
  const [port, setPort] = useState('5000')
  const [url, setUrl] = useState('')
  const [yaml, setYaml] = useState('')
  const [path, setPath] = useState('')
  const [approver, setApprover] = useState(() => localStorage.getItem('sentri.name') ?? '')
  const [planJob, setPlanJob] = useState<Job | null>(null)
  const [check, setCheck] = useState<{ valid: boolean; errors: string[] } | null>(null)
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)
  const [busy, setBusy] = useState(false)

  const run = async (fn: () => Promise<void>) => {
    setBusy(true)
    setMsg(null)
    try {
      await fn()
    } catch (e) {
      setMsg({ ok: false, text: (e as Error).message })
    } finally {
      setBusy(false)
    }
  }
  const doInspect = () => run(async () => {
    const r = await api.inspect(folder)
    setInspect(r)
    const s = r.suggestions[0]
    if (s?.run) setRunCmd(s.run)
    if (s?.port) setPort(String(s.port))
    if (r.contracts[0]) setPath(r.contracts[0])
    else setPath(`${r.path.replace(/[\\/]$/, '')}${r.path.includes('\\') ? '\\' : '/'}contract.yaml`)
  })
  const draft = () => run(async () => {
    const body = mode === 'url' ? { path: folder, url } : { path: folder, run: runCmd, port: Number(port) }
    setPlanJob(await api.plan(body))
  })
  useEffect(() => {
    if (!planJob || planJob.status === 'finished' || planJob.status === 'failed') return
    const t = setInterval(async () => {
      try {
        const j = await api.job(planJob.id)
        setPlanJob(j)
        if (j.status === 'finished' && j.result?.yaml) setYaml(String(j.result.yaml))
      } catch {
        /* the next tick retries; a lost server shows in the global banner */
      }
    }, 1500)
    return () => clearInterval(t)
  }, [planJob])
  const validate = () => run(async () => setCheck(await api.validate(yaml)))
  const save = () => run(async () => {
    localStorage.setItem('sentri.name', approver)
    const r = await api.saveContract(path, yaml, approver.trim() || null)
    if (r.approved) {
      setMsg({ ok: true, text: `Saved and approved by ${approver} at ${r.path}` })
      onApproved(String(r.path))
    } else setMsg({ ok: true, text: `Saved (not approved) at ${r.path}` })
  })

  return (
    <Panel title="Where is your app, and what may Sentri test?" hint="approving a contract is a human decision">
      <div className="stack">
        <div className="field">
          <label htmlFor="folder">Project folder</label>
          <div className="input-row">
            <input id="folder" className="input mono" value={folder} onChange={(e) => setFolder(e.target.value)} placeholder="D:\projects\my-app" />
            <button className="btn" disabled={busy || !folder.trim()} onClick={doInspect}>Inspect folder</button>
          </div>
          <span className="help">Used to suggest a start command and to find existing contracts. Its files never reach the tester.</span>
        </div>
        {inspect && (
          <>
            {inspect.contracts.length > 0 && (
              <Banner title="Existing contracts found">
                {inspect.contracts.map((c) => (
                  <button key={c} className="btn ghost small mono" onClick={() => { setPath(c); setMsg({ ok: true, text: `Edit or approve ${c} below; paste its YAML to validate.` }) }}>{c}</button>
                ))}
              </Banner>
            )}
            <div className="row" role="radiogroup" aria-label="How Sentri reaches the app">
              <label className="choice"><input type="radio" checked={mode === 'run'} onChange={() => setMode('run')} /><span><span className="title">Sentri starts the app</span><br /><span className="desc">restarted for every attempt: a clean in-memory state</span></span></label>
              <label className="choice"><input type="radio" checked={mode === 'url'} onChange={() => setMode('url')} /><span><span className="title">It is already running (URL)</span><br /><span className="desc">no reset: replays run on its live state</span></span></label>
            </div>
            <div className="form-grid">
              {mode === 'run' ? (
                <>
                  <div className="field"><label htmlFor="run-cmd">Start command</label><input id="run-cmd" className="input mono" value={runCmd} onChange={(e) => setRunCmd(e.target.value)} /></div>
                  <div className="field"><label htmlFor="port">Port</label><input id="port" className="input mono" inputMode="numeric" value={port} onChange={(e) => setPort(e.target.value)} /></div>
                </>
              ) : (
                <div className="field"><label htmlFor="url">App URL</label><input id="url" className="input mono" value={url} onChange={(e) => setUrl(e.target.value)} placeholder="http://127.0.0.1:3000" /></div>
              )}
            </div>
            <div className="row">
              <button className="btn small" disabled={busy || (planJob != null && !['finished', 'failed'].includes(planJob.status))} onClick={draft}>Draft a contract</button>
              {planJob && <span className="small muted" role="status">Draft {planJob.status}{planJob.error ? `: ${planJob.error}` : ''}</span>}
            </div>
          </>
        )}
        <div className="field">
          <label htmlFor="yaml">Contract (YAML)</label>
          <textarea id="yaml" className="textarea" spellCheck={false} value={yaml} onChange={(e) => { setYaml(e.target.value); setCheck(null) }} placeholder="Draft one above, or paste a contract here." />
        </div>
        {check && (check.valid ? <Banner title="The contract is valid" /> : <Banner tone="bad" title="The contract has problems">{check.errors.map((e) => <div key={e} className="mono xs">{e}</div>)}</Banner>)}
        <div className="form-grid">
          <div className="field"><label htmlFor="path">Save as</label><input id="path" className="input mono" value={path} onChange={(e) => setPath(e.target.value)} placeholder="…/contract.yaml" /></div>
          <div className="field"><label htmlFor="approver">Approve as (your name)</label><input id="approver" className="input" value={approver} onChange={(e) => setApprover(e.target.value)} placeholder="Leave empty to save without approving" /></div>
        </div>
        <div className="row">
          <button className="btn small" disabled={busy || !yaml.trim()} onClick={validate}>Validate</button>
          <button className="btn small" disabled={busy || !yaml.trim() || !path.trim()} onClick={save}>{approver.trim() ? 'Save and approve' : 'Save'}</button>
          {msg && <span className="small wrap-anywhere" role="status" style={{ color: msg.ok ? 'var(--ok)' : 'var(--bad)' }}>{msg.text}</span>}
        </div>
      </div>
    </Panel>
  )
}

function RunStep({ contractPath, approved }: { contractPath: string; approved: boolean }) {
  const [path, setPath] = useState(contractPath)
  const [mode, setMode] = useState<'agentic' | 'baseline'>('agentic')
  const [job, setJob] = useState<Job | null>(null)
  const [err, setErr] = useState<string | null>(null)
  useEffect(() => {
    if (!job) return
    if (job.run_id) {
      go(`/runs/${job.run_id}`)
      return
    }
    if (job.status === 'failed') return
    const t = setInterval(async () => {
      try {
        setJob(await api.job(job.id))
      } catch {
        /* retried next tick */
      }
    }, 1000)
    return () => clearInterval(t)
  }, [job])
  const start = async () => {
    setErr(null)
    try {
      setJob(await api.startRun({ contract: path, mode }))
    } catch (e) {
      setErr((e as Error).message)
    }
  }
  return (
    <Panel title="Start the run" hint="one run at a time; it continues if you leave this page">
      <div className="stack">
        <div className="form-grid">
          <div className="field"><label htmlFor="run-contract">Approved contract</label><input id="run-contract" className="input mono" value={path} onChange={(e) => setPath(e.target.value)} placeholder="…/contract.yaml" /></div>
          <div className="field">
            <label htmlFor="run-mode">Mode</label>
            <select id="run-mode" className="select" value={mode} onChange={(e) => setMode(e.target.value as 'agentic' | 'baseline')}>
              <option value="agentic">Agentic: explore with the model, then the baseline suite</option>
              <option value="baseline">Baseline only: the six fixed attacks</option>
            </select>
          </div>
        </div>
        {!approved && <p className="small faint">A contract approved in step 2 is filled in here; an already approved contract path works too.</p>}
        <div className="row">
          <button className="btn primary" disabled={!path.trim() || (job != null && job.status !== 'failed')} onClick={start}>
            <Play size={15} aria-hidden="true" /> Start run
          </button>
          {job && !job.run_id && job.status !== 'failed' && <span className="small muted" role="status">Starting… ({job.status})</span>}
          {job?.status === 'failed' && <span className="small" role="alert" style={{ color: 'var(--bad)' }}>{job.error}</span>}
          {err && <span className="small wrap-anywhere" role="alert" style={{ color: 'var(--bad)' }}>{err}</span>}
        </div>
      </div>
    </Panel>
  )
}
