import { classLabel, kesCompact, kesExact, metricOf, pct } from "../format";

export function BuildingRiskCard({ building, tier, assumption, onClose }) {
  const metric = metricOf(building, assumption, tier.id);
  return (
    <aside className="risk-card">
      <button type="button" className="text-button" onClick={onClose}>
        Close
      </button>
      <h2>{building.loc_id}</h2>
      <p className="card-class">{classLabel(building.housing_class)}</p>
      <div className="card-loss">
        <span>Estimated loss</span>
        <strong>{kesCompact(metric.loss_kes)}</strong>
        <em>{kesExact(metric.loss_kes)}</em>
      </div>
      <dl>
        <dt>TIV</dt>
        <dd>{kesCompact(building.tiv_kes)}</dd>
        <dt>Susceptibility</dt>
        <dd>{Number(metric.hazard_score).toLocaleString("en-US", { maximumFractionDigits: 4 })}</dd>
        <dt>Damage ratio</dt>
        <dd>{pct(metric.damage_ratio, 2)}</dd>
        <dt>Flood tier</dt>
        <dd>
          {tier.label} · {tier.assumed_return_period_years}Y
        </dd>
      </dl>
      <p className="card-note">Synthetic exposure</p>
      <p className="card-note">Susceptibility proxy{metric.affected ? "" : " · not proxy-flagged"}</p>
    </aside>
  );
}
