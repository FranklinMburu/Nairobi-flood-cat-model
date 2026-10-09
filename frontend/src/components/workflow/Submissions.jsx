import { useCallback, useEffect, useState } from "react";
import { PageHeader } from "../PageHeader";
import { createSubmission, getCapabilities, listSubmissions } from "../../services/api";
import { ModeBadge, StatusBadge } from "./badges";
import { SubmissionDetail } from "./SubmissionDetail";

/** Checkpoint 8: free-text exposure submissions, from extraction to an approved model run. */
export function Submissions({ user }) {
  const [items, setItems] = useState(null);
  const [listError, setListError] = useState("");
  const [capabilities, setCapabilities] = useState(null);
  const [selectedId, setSelectedId] = useState(null);

  const refresh = useCallback(async () => {
    setListError("");
    try {
      const body = await listSubmissions();
      setItems(body.submissions);
    } catch (reason) {
      setListError(reason.message);
    }
  }, []);

  useEffect(() => {
    refresh();
    getCapabilities().then(setCapabilities).catch(() => setCapabilities(null));
  }, [refresh]);

  function opened(submission) {
    setSelectedId(submission.id);
    refresh();
  }

  return (
    <section className="page wide">
      <PageHeader
        title="Submissions"
        subtitle="Free-text exposure submissions: AI extraction, deterministic verification, human approval, then the loss engine."
      />
      <NewSubmission capabilities={capabilities} onCreated={opened} />
      <div className="wf-split">
        <div className="panel wf-list">
          <h2>Recorded submissions</h2>
          {listError ? (
            <p className="auth-error" role="alert">
              {listError} <button type="button" className="text-button" onClick={refresh}>Retry</button>
            </p>
          ) : null}
          {items === null && !listError ? <p className="fine">Loading…</p> : null}
          {items && items.length === 0 ? <p className="fine">No submissions yet. Process the bundled example or submit a text above.</p> : null}
          {items && items.length > 0 ? (
            <ul>
              {items.map((item) => (
                <li key={item.id}>
                  <button
                    type="button"
                    className={item.id === selectedId ? "wf-item on" : "wf-item"}
                    onClick={() => setSelectedId(item.id)}
                  >
                    <span>
                      <StatusBadge status={item.status} />
                      {item.extraction ? <ModeBadge mode={item.extraction.execution_mode} /> : null}
                    </span>
                    <strong>{item.document ? item.document.metadata.original_filename || item.document.origin : item.id}</strong>
                    <small>{item.id}</small>
                  </button>
                </li>
              ))}
            </ul>
          ) : null}
        </div>
        <div className="wf-detail">
          {selectedId ? (
            <SubmissionDetail key={selectedId} id={selectedId} user={user} onChanged={refresh} />
          ) : (
            <div className="panel">
              <p className="fine">Select a submission to see its source text, extraction, verification and decisions.</p>
            </div>
          )}
        </div>
      </div>
    </section>
  );
}

function NewSubmission({ capabilities, onCreated }) {
  const [text, setText] = useState("");
  const [file, setFile] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function submit(source) {
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      const created = await createSubmission(
        source === "example" ? { example: true } : source === "file" ? { file } : { text },
      );
      setText("");
      setFile(null);
      onCreated(created);
    } catch (reason) {
      setError(reason.message);
    } finally {
      setBusy(false);
    }
  }

  const live = capabilities && capabilities.live_ai.configured;
  return (
    <div className="panel">
      <h2>New submission</h2>
      <p className="fine">
        {capabilities === null
          ? "Checking what the server can process…"
          : live
            ? `Live extraction: ${capabilities.live_ai.provider} (${capabilities.live_ai.model}). The bundled example is always replayed.`
            : "No live AI provider is configured on the server. Only the bundled example can be processed; its AI response is a hand-written replay, not a live model output."}
      </p>
      <div className="wf-actions">
        <button type="button" className="send" disabled={busy || !(capabilities && capabilities.replay_example)} onClick={() => submit("example")}>
          {busy ? "Working…" : "Process bundled example (replay)"}
        </button>
      </div>
      <div className="wf-new">
        <label>
          Text file (UTF-8, up to 1 MB)
          <input type="file" accept=".txt,text/plain" onChange={(event) => setFile(event.target.files[0] || null)} />
        </label>
        <button type="button" className="send" disabled={busy || !file} onClick={() => submit("file")}>
          Submit file
        </button>
      </div>
      <div className="wf-new">
        <label>
          Or paste the submission text
          <textarea rows={4} value={text} onChange={(event) => setText(event.target.value)} />
        </label>
        <button type="button" className="send" disabled={busy || !text.trim()} onClick={() => submit("text")}>
          Submit text
        </button>
      </div>
      <p className="fine">
        Structured files (CSV, XLSX, PDF tables, GeoJSON) go through File upload instead.
      </p>
      {error ? <p className="auth-error" role="alert">{error}</p> : null}
    </div>
  );
}
