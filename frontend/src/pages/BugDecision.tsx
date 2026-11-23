import { useState } from 'react'
import { api, type Bug } from '../api'
import { Badge, BugBadge, href } from '../ui'

function storedName(): string {
  try {
    return localStorage.getItem('sentri.name') ?? ''
  } catch {
    return ''
  }
}
function storeName(name: string) {
  try {
    localStorage.setItem('sentri.name', name)
  } catch {
    /* a convenience only */
  }
}

/** The human decision on one bug. Marking it intended needs a reason; Sentri itself never does this. */
export function BugDecision({ bug, onChanged, showLink = false }: { bug: Bug; onChanged: () => void; showLink?: boolean }) {
  const [note, setNote] = useState('')
  const [by, setBy] = useState(storedName)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const target = bug.status === 'intended' ? 'open' : 'intended'
  const submit = async () => {
    setBusy(true)
    setErr(null)
    try {
      storeName(by)
      await api.setBugStatus(bug.id, target, note, by || 'dashboard')
      setNote('')
      onChanged()
    } catch (e) {
      setErr((e as Error).message)
    } finally {
      setBusy(false)
    }
  }
  return (
    <div className="stack" style={{ gap: 12 }}>
      <div className="row">
        {showLink ? <a className="mono" href={href(`bugs/${bug.id}`)}>{bug.id}</a> : <span className="mono">{bug.id}</span>}
        <BugBadge status={bug.status} />
        <span className="mono xs faint">{bug.flow_id} × {bug.attack_id} · {bug.oracle_id}</span>
        {bug.regressions > 0 && <Badge tone="bad">came back {bug.regressions}×</Badge>}
      </div>
      {bug.human_note && <p className="small muted">Recorded decision: {bug.human_note}</p>}
      <div className="form-grid">
        <div className="field">
          <label htmlFor={`note-${bug.id}`}>{target === 'intended' ? 'Why is this intended?' : 'Why reopen it?'}</label>
          <input id={`note-${bug.id}`} className="input" value={note} onChange={(e) => setNote(e.target.value)} placeholder={target === 'intended' ? 'Required: the reason, for the record' : 'Optional'} />
        </div>
        <div className="field">
          <label htmlFor={`by-${bug.id}`}>Your name</label>
          <input id={`by-${bug.id}`} className="input" value={by} onChange={(e) => setBy(e.target.value)} placeholder="Recorded with the decision" />
        </div>
      </div>
      <div className="row">
        <button className="btn" disabled={busy || (target === 'intended' && !note.trim())} onClick={submit}>
          {target === 'intended' ? 'Mark intended' : 'Reopen'}
        </button>
        <span className="faint small">{target === 'intended' ? 'It stays in the ledger and stops counting against readiness.' : 'It counts again and is re-checked on later runs.'}</span>
        {err && <span className="small" role="alert" style={{ color: 'var(--bad)' }}>{err}</span>}
      </div>
    </div>
  )
}
