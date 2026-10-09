import { EventSelector } from "./EventSelector";
import { LossCurve } from "./LossCurve";
import { PageHeader } from "./PageHeader";
import { classLabel, currentScenario, kesCompact, kesExact, pct, tierStats } from "../format";

export function LossAnalysis({ data, tier, assumption }) {
  const damage = currentScenario(data, assumption);
  const stats = tierStats(data, assumption, tier);
  const classes = data.class_summary[assumption][tier];

  return (
    <section className="page">
      <PageHeader
        title="Estimated Portfolio Loss by Assumed Return Period"
        subtitle={`${damage.short_label} damage assumption, H = ${damage.h}. Not an empirical EP curve. Not calculated from the hazard rasters.`}
      />
      <EventSelector data={data} tier={tier} assumption={assumption} />
      <div className="panel">
        <LossCurve data={data} assumption={assumption} tier={tier} tall />
        <p className="fine">
          {stats.affected_buildings} proxy-flagged buildings at this event. Loss ratio {pct(stats.loss_pct_portfolio, 4)}.
        </p>
      </div>
      <div className="panel">
        <h2>Loss by housing class</h2>
        <table>
          <thead>
            <tr>
              <th>Class</th>
              <th>Proxy-flagged</th>
              <th>Estimated loss</th>
              <th>Share of tier</th>
            </tr>
          </thead>
          <tbody>
            {classes.map((row) => (
              <tr key={row.housing_class}>
                <td>{classLabel(row.housing_class)}</td>
                <td>{row.affected_buildings} of {row.buildings}</td>
                <td title={kesExact(row.loss_kes)}>{kesCompact(row.loss_kes)}</td>
                <td>{pct(row.share_of_tier_loss || 0, 1)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
