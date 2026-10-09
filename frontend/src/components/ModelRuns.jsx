import { useEffect, useState } from "react";
import { PageHeader } from "./PageHeader";
import { kesExact } from "../format";
import { listRuns, startReferenceRun } from "../services/api";
import { Hash } from "./workflow/badges";

const TIERS = ["extreme", "severe", "moderate", "occasional", "common"];

/** Recorded runs of the supplied 600-building portfolio (loss_engine.run_record). */
export function ModelRuns() {
  const [runs, setRuns] = useState(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [selected, setSelected] = useState(null);
  const [scenario, setScenario] = useState("reference");

  async function refresh() {
    setError("");
    try {
      const body = await listRuns();
      setRuns(body.runs);
      if (!selected && body.runs.length) setSelected(body.runs[0]);
    } catch (reason) {
      setError(reason.message);
    }
  }

  useEffect(() => {
    refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function start() {
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      const record = await startReferenceRun();
      setSelected(record);
      await refresh();
    } catch (reason) {
      setError(reason.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="page wide">
      <PageHeader title="Model runs" subtitle="Recorded runs of the supplied synthetic portfolio, with full provenance.">
        <button type="button" className="send" disabled={busy} onClick={start}>
          {busy ? "Running…" : "Run supplied portfolio"}
        </button>
      </PageHeader>
      {error ? <p className="auth-error" role="alert">{error}</p> : null}
      <div className="wf-split">
        <div className="panel wf-list">
          <h2>Recorded runs</h2>
          {runs === null && !error ? <p className="fine">Loading…</p> : null}
          {runs && runs.length === 0 ? <p className="fine">No recorded runs yet. Start one to create a run record.</p> : null}
          <ul>
            {(runs || []).map((run) => (
              <li key={run.run_id}>
                <button type="button" className={selected && selected.run_id === run.run_id ? "wf-item on" : "wf-item"} onClick={() => setSelected(run)}>
                  <strong>{run.run_id}</strong>
                  <small>{run.status} · {run.timestamp}</small>
                </button>
              </li>
            ))}
          </ul>
        </div>
        <div className="wf-detail">
          {selected ? <RunRecord run={selected} scenario={scenario} onScenario={setScenario} /> : null}
        </div>
      </div>
    </section>
  );
}

function RunRecord({ run, scenario, onScenario }) {
  const scenarios = [...new Set(run.tier_summary.map((r) => r.scenario_id))];
  const tiers = run.tier_summary.filter((r) => r.scenario_id === scenario);
  const ep = run.ep_points.filter((r) => r.scenario_id === scenario);
  const counts = run.validation_results.reduce((acc, v) => ({ ...acc, [v.status]: (acc[v.status] || 0) + 1 }), {});
  return (
    <div className="panel">
      <h2>{run.run_id}</h2>
      <dl className="wf-facts">
        <dt>Status</dt><dd>{run.status}{run.failure ? ` at ${run.failed_at}: ${run.failure}` : ""}</dd>
        <dt>Input</dt><dd>{run.input_filename} <Hash value={run.input_sha256} /> · {run.row_count} buildings · TIV {run.total_tiv_kes ? kesExact(run.total_tiv_kes, 0) : "—"}</dd>
        <dt>Parameters</dt><dd><Hash value={run.parameter_set_id} /> · return periods <Hash value={run.mapping_id} /></dd>
        <dt>Model / code</dt><dd>{run.model_version} · commit {run.code_version.git_commit ? run.code_version.git_commit.slice(0, 10) : "unknown"}{run.code_version.git_dirty ? " (uncommitted changes)" : ""}</dd>
        <dt>Record file</dt><dd><Hash value={run.record_sha256} /></dd>
        <dt>Rule results</dt><dd>{Object.entries(counts).map(([k, v]) => `${v} ${k}`).join(", ")}</dd>
      </dl>
      {scenarios.length ? (
        <div className="segments" role="radiogroup" aria-label="Damage assumption">
          {scenarios.map((id) => (
            <button key={id} type="button" className={id === scenario ? "on" : ""} onClick={() => onScenario(id)}>{id}</button>
          ))}
        </div>
      ) : null}
      {tiers.length ? (
        <div className="admin-table-wrap">
          <table className="admin-table">
            <thead><tr><th>Tier</th><th>Assumed return period</th><th>Portfolio loss</th><th>Affected buildings</th><th>Affected TIV</th></tr></thead>
            <tbody>
              {TIERS.map((tier) => {
                const row = tiers.find((r) => r.tier === tier);
                const point = ep.find((p) => p.tier === tier);
                if (!row) return null;
                return (
                  <tr key={tier}>
                    <td>{tier}</td>
                    <td>{point ? `${point.return_period_years} years (AEP ${point.annual_exceedance_probability})` : "—"}</td>
                    <td>{kesExact(row.portfolio_loss_kes)}</td>
                    <td>{row.affected_buildings}</td>
                    <td>{kesExact(row.affected_tiv_kes, 0)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : null}
      <ul className="fine">
        <li>{run.synthetic_proxy_statement}</li>
        {run.assumption_statements.map((s) => <li key={s}>{s}</li>)}
      </ul>
    </div>
  );
}
