import { PageHeader } from "./PageHeader";
import { classLabel, currentScenario, currentTier, kesExact, pct, tierStats } from "../format";

export function Reports({ data, tier, assumption }) {
  const damage = currentScenario(data, assumption);
  const event = currentTier(data, tier);
  const stats = tierStats(data, assumption, tier);
  const classes = data.class_summary[assumption][tier];

  return (
    <section className="page">
      <PageHeader title="Reports" subtitle="Figures are the loss engine results for the current event.">
        <button type="button" className="send" onClick={() => window.print()}>
          Generate Risk Report
        </button>
      </PageHeader>
      <article className="panel report">
        <h2>Nairobi urban flood risk</h2>
        <p>Synthetic portfolio. Susceptibility proxy, not flood depth. Damage parameter H = {damage.h} is an assumption. Return periods are provisional D-004 labels.</p>
        <p>
          {damage.short_label} · {event.label} · assumed {event.assumed_return_period_years}-year
        </p>
        <div className="kpi-row">
          <article className="kpi">
            <span>Portfolio TIV</span>
            <strong>{kesExact(stats.tiv_kes, 0)}</strong>
          </article>
          <article className="kpi">
            <span>Estimated loss</span>
            <strong>{kesExact(stats.portfolio_loss_kes)}</strong>
          </article>
          <article className="kpi">
            <span>Loss ratio</span>
            <strong>{pct(stats.loss_pct_portfolio, 4)}</strong>
          </article>
        </div>
        <h2>Assumed return periods</h2>
        <table>
          <tbody>
            {data.tiers.map((item) => (
              <tr key={item.id}>
                <td>{item.assumed_return_period_years}Y · {item.label}</td>
                <td>{kesExact(data.tier_summary[assumption][item.id].portfolio_loss_kes)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <h2>Housing class</h2>
        <table>
          <tbody>
            {classes.map((row) => (
              <tr key={row.housing_class}>
                <td>{classLabel(row.housing_class)}</td>
                <td>{kesExact(row.loss_kes)}</td>
                <td>{pct(row.share_of_tier_loss || 0, 1)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="fine">Model parameter set {data.parameter_set_id}. Affected means proxy-flagged: {stats.affected_buildings} buildings.</p>
      </article>
    </section>
  );
}
