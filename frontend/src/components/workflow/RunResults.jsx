import { useMemo, useState } from "react";
import { classLabel, kesExact } from "../../format";
import { Hash } from "./badges";

const TIERS = ["extreme", "severe", "moderate", "occasional", "common"];

/** Results of an approved workflow run, exactly as saved by the backend. */
export function RunResults({ run }) {
  const scenarios = useMemo(() => [...new Set(run.comparison.map((r) => r.scenario_id))], [run]);
  const [scenario, setScenario] = useState(scenarios.includes("reference") ? "reference" : scenarios[0]);
  const record = run.workflow_record;
  const rows = run.comparison.filter((r) => r.scenario_id === scenario);
  const ep = run.ep_comparison.filter((r) => r.scenario_id === scenario);

  return (
    <div className="wf-results">
      <p className="fine">
        Structural ground-up loss in KES. Synthetic portfolio; the hazard is a susceptibility proxy, not a flood depth
        or probability. Every figure comes from the engine's output tables.
      </p>
      <div className="segments" role="radiogroup" aria-label="Damage assumption">
        {scenarios.map((id) => (
          <button key={id} type="button" className={id === scenario ? "on" : ""} onClick={() => setScenario(id)}>{id}</button>
        ))}
      </div>

      <h3>Portfolio loss by hazard tier</h3>
      <div className="admin-table-wrap">
        <table className="admin-table">
          <thead><tr><th>Tier</th><th>Supplied portfolio</th><th>New account alone</th><th>Portfolio with account</th><th>Marginal change</th><th>Account buildings affected</th></tr></thead>
          <tbody>
            {TIERS.map((tier) => {
              const r = rows.find((row) => row.tier === tier);
              if (!r) return null;
              return (
                <tr key={tier}>
                  <td>{tier}</td>
                  <td>{kesExact(r.baseline_portfolio_loss_kes)}</td>
                  <td>{kesExact(r.account_portfolio_loss_kes)}</td>
                  <td>{kesExact(r.with_account_portfolio_loss_kes)}</td>
                  <td>{kesExact(r.marginal_portfolio_loss_kes)}</td>
                  <td>{Number(r.account_affected_buildings)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      <h3>Loss at the provisional return periods (D-004)</h3>
      <p className="fine">Five scenario points with assumed return periods; not a stochastic or calibrated EP curve. No EAL, PML or TVaR is computed.</p>
      <div className="admin-table-wrap">
        <table className="admin-table">
          <thead><tr><th>Return period</th><th>AEP</th><th>Supplied portfolio</th><th>With account</th><th>Marginal</th></tr></thead>
          <tbody>
            {ep.map((r) => (
              <tr key={r.tier}>
                <td>{r.return_period_years} years ({r.tier})</td>
                <td>{Number(r.annual_exceedance_probability)}</td>
                <td>{kesExact(r.baseline_loss_kes)}</td>
                <td>{kesExact(r.with_account_loss_kes)}</td>
                <td>{kesExact(r.marginal_loss_kes)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <h3>Account buildings and their sampled hazard scores</h3>
      <p className="fine">Scores (0 to 1) read from the supplied rasters for each building's location; susceptibility proxies, not depths.</p>
      <div className="admin-table-wrap">
        <table className="admin-table">
          <thead><tr><th>loc_id</th><th>Lat, lon</th><th>Class</th><th>TIV (KES)</th>{TIERS.map((t) => <th key={t}>{t}</th>)}</tr></thead>
          <tbody>
            {run.account_exposure.map((b) => (
              <tr key={b.loc_id}>
                <td>{b.loc_id}</td>
                <td>{b.lat}, {b.lon}</td>
                <td>{classLabel(b.housing_class)}</td>
                <td>{kesExact(b.tiv_kes, 0)}</td>
                {TIERS.map((t) => <td key={t}>{Number(b[`hazard_score_${t}`]).toFixed(4)}</td>)}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <h3>Where each value came from</h3>
      <ul className="wf-origins">
        {record.exposure.value_origins.map((o) => (
          <li key={o.loc_id}>
            <strong>{o.loc_id}</strong> ({o.item_id}): location {o.lat_lon}; class {o.housing_class}; count {o.building_count}; value {o.tiv_kes}
          </li>
        ))}
      </ul>

      {record.unmodelled_terms.length ? (
        <>
          <h3>Policy terms not modelled</h3>
          <ul>
            {record.unmodelled_terms.map((t, i) => (
              <li key={i}>{t.item_id} ({t.item_status}): {t.kind} — <q>{t.quote}</q></li>
            ))}
          </ul>
        </>
      ) : null}
      {record.approval.excluded_items.length ? <p className="fine">Excluded items: {record.approval.excluded_items.join(", ")}.</p> : null}

      <h3>Provenance</h3>
      <dl className="wf-facts">
        <dt>Document</dt><dd>{record.source_document.document_id} <Hash value={record.source_document.text_sha256} /> · original file <Hash value={record.source_document.original_file_sha256} /></dd>
        <dt>Extraction</dt><dd>{record.ai_extraction.extraction_id} · {record.ai_extraction.provider}/{record.ai_extraction.model} · {record.ai_extraction.execution_mode}{record.ai_extraction.replay_of ? ` of ${record.ai_extraction.replay_of}` : ""}</dd>
        <dt>Verification</dt><dd>{record.verification.outcome} <Hash value={record.verification.report_sha256} /></dd>
        <dt>Approval</dt><dd>{record.approval.approval_id} by {record.approval.approver} · candidate <Hash value={record.approval.candidate_sha256} /> · file <Hash value={record.approval.approval_sha256} /></dd>
        <dt>Hazard rasters</dt><dd>raster set <Hash value={record.hazard.raster_set_id} /></dd>
        {Object.entries(record.runs).map(([name, r]) => (
          <div key={name} className="wf-fact-row">
            <dt>Run: {name}</dt>
            <dd>{r.run_id} · input <Hash value={r.input_sha256} /> · record <Hash value={r.record_sha256} /> · parameters <Hash value={r.parameter_set_id} /></dd>
          </div>
        ))}
      </dl>
      <ul className="fine">
        {record.statements.map((s) => <li key={s}>{s}</li>)}
      </ul>
    </div>
  );
}
