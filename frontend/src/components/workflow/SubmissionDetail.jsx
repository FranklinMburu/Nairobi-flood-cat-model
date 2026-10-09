import { useCallback, useEffect, useState } from "react";
import { getSubmission, runSubmission } from "../../services/api";
import { Hash, ModeBadge, ResultBadge, StatusBadge } from "./badges";
import { ReviewForm } from "./ReviewForm";
import { RunResults } from "./RunResults";

export function SubmissionDetail({ id, user, onChanged }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [running, setRunning] = useState(false);
  const [runError, setRunError] = useState("");

  const load = useCallback(async () => {
    setError("");
    try {
      setData(await getSubmission(id));
    } catch (reason) {
      setError(reason.message);
    }
  }, [id]);

  useEffect(() => {
    load();
  }, [load]);

  function updated(next) {
    setData(next);
    onChanged();
  }

  async function run() {
    if (running) return;
    setRunning(true);
    setRunError("");
    try {
      updated(await runSubmission(id));
    } catch (reason) {
      setRunError(reason.message);
    } finally {
      setRunning(false);
    }
  }

  if (error) {
    return (
      <div className="panel">
        <p className="auth-error" role="alert">{error}</p>
        <button type="button" className="text-button" onClick={load}>Retry</button>
      </div>
    );
  }
  if (!data) return <div className="panel"><p className="fine">Loading submission…</p></div>;

  const { document, extraction } = data;
  const reviewable = ["awaiting_approval", "review_requested", "rejected_by_reviewer", "stale_approval", "approved"].includes(data.status);
  const latest = data.decisions.length ? data.decisions[data.decisions.length - 1] : null;
  const canRun = data.status === "approved";

  return (
    <>
      <div className="panel">
        <div className="wf-head">
          <h2>Submission {data.id}</h2>
          <span>
            <StatusBadge status={data.status} /> <ModeBadge mode={extraction.execution_mode} />
          </span>
        </div>
        {extraction.execution_mode === "replay" ? (
          <p className="wf-note">
            This AI response is a replay of a recorded response ({extraction.replay_of}). For the bundled example the
            recording was written by hand; no AI provider was called.
          </p>
        ) : null}
        {!data.verification_consistent ? (
          <p className="auth-error" role="alert">The saved verification report no longer matches a fresh verification. No decision or run is allowed.</p>
        ) : null}
        <dl className="wf-facts">
          <dt>Original file</dt><dd>{document.metadata.original_filename || "—"} <Hash value={document.metadata.original_file_sha256} /></dd>
          <dt>Uploaded by</dt><dd>{document.metadata.uploaded_by || "—"}</dd>
          <dt>Document</dt><dd>{document.document_id} · text <Hash value={document.sha256} /> · received {document.received_at}</dd>
          <dt>Extraction</dt><dd>{extraction.extraction_id} · {extraction.provider} / {extraction.model} · status {extraction.status}</dd>
          <dt>Prompt / schema</dt><dd>{extraction.prompt_version} <Hash value={extraction.prompt_sha256} /> · {extraction.schema_version} <Hash value={extraction.schema_sha256} /></dd>
          <dt>Response</dt><dd><Hash value={extraction.response_sha256} /></dd>
        </dl>
        {extraction.error ? <p className="auth-error" role="alert">Provider error: {extraction.error}</p> : null}
      </div>

      <div className="panel">
        <h2>Source text, exactly as received</h2>
        <pre className="wf-source">{document.text}</pre>
      </div>

      {data.candidate ? <Candidate candidate={data.candidate} checks={data.verification.checks} /> : null}

      <div className="panel">
        <h2>Verification: {data.verification_outcome.replace("_", " ")}</h2>
        <p className="fine">Deterministic checks of every quote and value against the source text. The verifier never approves; a human decides.</p>
        <Checks checks={data.verification.checks} />
      </div>

      {data.decisions.length ? (
        <div className="panel">
          <h2>Decisions</h2>
          <table className="admin-table">
            <thead><tr><th>When</th><th>Decision</th><th>By</th><th>Reason</th><th>Excluded</th><th>Corrections</th><th>Valid for this candidate</th></tr></thead>
            <tbody>
              {data.decisions.map((d) => (
                <tr key={d.approval_id}>
                  <td>{d.decided_at}</td>
                  <td>{d.decision}</td>
                  <td>{d.approver}</td>
                  <td>{d.reason || "—"}</td>
                  <td>{d.excluded_items.join(", ") || "—"}</td>
                  <td>{d.corrections.map((c) => `${c.item_id}.${c.field} = ${c.value} (${c.reason})`).join("; ") || "—"}</td>
                  <td>{d.current ? "yes" : "no (stale)"}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="fine">Approver names are the signed-in account. The approval record does not verify identity (approver_verified is false).</p>
        </div>
      ) : null}

      {reviewable && data.requirements.approvable && data.status !== "approved" ? (
        <ReviewForm submission={data} user={user} onDecided={updated} />
      ) : null}
      {!data.requirements.approvable ? (
        <div className="panel"><p className="fine">This submission cannot be approved: {data.requirements.reason || data.status}.</p></div>
      ) : null}

      <div className="panel">
        <h2>Model run</h2>
        {data.run ? null : (
          <>
            <p className="fine">
              {canRun
                ? "The approved items become exposure rows, their hazard scores are read from the rasters, and the loss engine runs on the supplied portfolio, the account, and both together."
                : "The model runs only after a current approval. The server checks this again on every request."}
            </p>
            <button type="button" className="send" disabled={!canRun || running} onClick={run}>
              {running ? "Running the engine…" : "Run the model"}
            </button>
          </>
        )}
        {runError ? <p className="auth-error" role="alert">{runError}</p> : null}
        {latest && data.run ? <RunResults run={data.run} /> : null}
      </div>
    </>
  );
}

function field(item, key) {
  const value = item[key];
  if (!value) return "—";
  const shown = value.value ?? value.amount ?? value.text;
  const extra = key === "insured_value" ? ` ${value.currency || "(no currency)"} · ${value.basis}` : "";
  const status = value.status ? ` · ${value.status}` : "";
  return `${shown ?? "missing"}${extra}${status}`;
}

function Candidate({ candidate, checks }) {
  const flagged = new Set(checks.filter((c) => c.result !== "pass").map((c) => `${c.item_id}|${c.field}`));
  return (
    <div className="panel">
      <h2>Extracted items (an untrusted AI candidate)</h2>
      <div className="admin-table-wrap">
        <table className="admin-table">
          <thead><tr><th>Item</th><th>Field</th><th>Value</th><th>Quote from the source</th></tr></thead>
          <tbody>
            {candidate.items.map((item) =>
              ["location", "housing_class", "building_count", "insured_value", "floor_area_m2", "cost_per_m2"].map((key, index) => (
                <tr key={`${item.item_id}-${key}`} className={flagged.has(`${item.item_id}|${key}`) ? "wf-flagged" : ""}>
                  <td>{index === 0 ? item.item_id : ""}</td>
                  <td>{key}</td>
                  <td>{key === "location" && item.location.lat != null ? `${item.location.text || ""} (${item.location.lat}, ${item.location.lon})` : field(item, key)}</td>
                  <td>{item[key] && item[key].quote ? <q>{item[key].quote}</q> : "—"}</td>
                </tr>
              )),
            )}
          </tbody>
        </table>
      </div>
      {candidate.items.some((item) => item.unmodelled_terms.length) ? (
        <>
          <h3>Policy terms (recorded, not modelled)</h3>
          <ul>
            {candidate.items.flatMap((item) =>
              item.unmodelled_terms.map((term, index) => (
                <li key={`${item.item_id}-${index}`}>{item.item_id}: {term.kind} — <q>{term.quote}</q></li>
              )),
            )}
          </ul>
        </>
      ) : null}
    </div>
  );
}

function Checks({ checks }) {
  const [showPassed, setShowPassed] = useState(false);
  const flagged = checks.filter((c) => c.result !== "pass");
  const shown = showPassed ? checks : flagged;
  return (
    <>
      {flagged.length === 0 ? <p className="fine">No check needs review.</p> : null}
      {shown.length ? (
        <div className="admin-table-wrap">
          <table className="admin-table">
            <thead><tr><th>Result</th><th>Item</th><th>Field</th><th>Check</th><th>Detail</th></tr></thead>
            <tbody>
              {shown.map((c, index) => (
                <tr key={index}>
                  <td><ResultBadge result={c.result} /></td>
                  <td>{c.item_id || "*"}</td>
                  <td>{c.field}</td>
                  <td>{c.check}</td>
                  <td>{c.detail}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
      <button type="button" className="text-button" onClick={() => setShowPassed(!showPassed)}>
        {showPassed ? "Show only checks that need attention" : `Show all ${checks.length} checks`}
      </button>
    </>
  );
}
