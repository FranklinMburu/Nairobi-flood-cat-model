import { useEffect, useState } from "react";
import { PageHeader } from "./PageHeader";
import { kesExact } from "../format";
import { getCapabilities, uploadExposureFile } from "../services/api";
import { Hash } from "./workflow/badges";

/**
 * Structured exposure files, handled by the upload service (api/main.py).
 * The browser sends the file to Django, which forwards it with the
 * service key; the service adapts the file and runs the engine.
 */
export function FileUpload() {
  const [capabilities, setCapabilities] = useState(null);
  const [file, setFile] = useState(null);
  const [synthetic, setSynthetic] = useState("");
  const [fxRate, setFxRate] = useState("");
  const [acceptUncertain, setAcceptUncertain] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState(null);

  useEffect(() => {
    getCapabilities().then(setCapabilities).catch((reason) => setError(reason.message));
  }, []);

  async function send(action) {
    if (busy || !file) return;
    setBusy(true);
    setError("");
    setResult(null);
    try {
      setResult({ action, ...(await uploadExposureFile(action, { file, synthetic, fxRate, acceptUncertain })) });
    } catch (reason) {
      setError(reason.message);
    } finally {
      setBusy(false);
    }
  }

  const configured = capabilities && capabilities.ingest.configured;
  return (
    <section className="page wide">
      <PageHeader title="File upload" subtitle="CSV, XLSX, GeoJSON points or PDF tables, adapted to the engine's exposure schema by the upload service." />
      <div className="panel">
        {capabilities && !configured ? (
          <p className="wf-note">The upload service is not configured on this server (ADAPTER_API_KEY), so uploads are unavailable.</p>
        ) : null}
        <div className="wf-new">
          <label>
            Exposure file (up to 10 MB)
            <input type="file" accept=".csv,.xlsx,.geojson,.json,.pdf" onChange={(e) => setFile(e.target.files[0] || null)} />
          </label>
          <label>
            Synthetic portfolio?
            <select value={synthetic} onChange={(e) => setSynthetic(e.target.value)}>
              <option value="">From the file's synthetic column</option>
              <option value="true">Yes, synthetic</option>
              <option value="false">No, real exposure</option>
            </select>
          </label>
          <label>
            KES per USD (only if the file has USD values)
            <input value={fxRate} inputMode="decimal" onChange={(e) => setFxRate(e.target.value)} />
          </label>
          <label className="wf-check">
            <input type="checkbox" checked={acceptUncertain} onChange={(e) => setAcceptUncertain(e.target.checked)} />
            Accept uncertain column matches
          </label>
        </div>
        <div className="wf-actions">
          <button type="button" className="send" disabled={busy || !file || !configured} onClick={() => send("adapt")}>Adapt only</button>
          <button type="button" className="send" disabled={busy || !file || !configured} onClick={() => send("run")}>
            {busy ? "Working…" : "Adapt and run the model"}
          </button>
        </div>
        {error ? <p className="auth-error" role="alert">{error}</p> : null}
      </div>
      {result ? <UploadResult result={result} /> : null}
    </section>
  );
}

function UploadResult({ result }) {
  const report = result.report || result.adaptation_report || {};
  const status = result.status || result.adapter_status;
  const reference = (result.tier_summary || []).filter((r) => r.scenario_id === "reference");
  return (
    <div className="panel">
      <h2>Adapter: {status}{result.run_status ? ` · model run: ${result.run_status}` : ""}</h2>
      {result.original_file ? (
        <p className="fine">
          {result.original_file.filename} <Hash value={result.original_file.sha256} /> · uploaded by {result.original_file.uploaded_by}
        </p>
      ) : null}
      {result.message ? <p className="wf-note">{result.message}</p> : null}
      {report.refusal_reasons && report.refusal_reasons.length ? (
        <>
          <h3>Why the file was refused</h3>
          <ul>{report.refusal_reasons.map((r) => <li key={r}>{r}</li>)}</ul>
        </>
      ) : null}
      {report.column_mappings && report.column_mappings.length ? (
        <>
          <h3>Column mapping</h3>
          <div className="admin-table-wrap">
            <table className="admin-table">
              <thead><tr><th>Source column</th><th>Mapped to</th><th>Method</th><th>Confidence</th></tr></thead>
              <tbody>
                {report.column_mappings.map((m, i) => (
                  <tr key={i}><td>{m.source_column}</td><td>{m.target_field}</td><td>{m.method}</td><td>{m.confidence}</td></tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      ) : null}
      {result.failure ? <p className="auth-error">Run stopped at {result.failed_at}: {result.failure}</p> : null}
      {reference.length ? (
        <>
          <h3>Reference scenario, portfolio loss (run {result.run_id})</h3>
          <p className="fine">{result.row_count} buildings · total TIV {kesExact(result.total_tiv_kes, 0)}. Synthetic or not as declared above; hazard scores as supplied in the file.</p>
          <div className="admin-table-wrap">
            <table className="admin-table">
              <thead><tr><th>Tier</th><th>Portfolio loss</th><th>Affected buildings</th></tr></thead>
              <tbody>
                {reference.map((r) => (
                  <tr key={r.tier}><td>{r.tier}</td><td>{kesExact(r.portfolio_loss_kes)}</td><td>{r.affected_buildings}</td></tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      ) : null}
      {result.adapted_csv ? <p className="fine">Adapted CSV returned ({result.adapted_csv.split("\n").length - 1} rows).</p> : null}
    </div>
  );
}
