import { EventSelector } from "./EventSelector";
import { LossCurve } from "./LossCurve";
import { PageHeader } from "./PageHeader";
import { currentScenario, currentTier, kesCompact, kesExact, pct, tierStats } from "../format";

export function Overview({ data, tier, assumption }) {
  const stats = tierStats(data, assumption, tier);
  const event = currentTier(data, tier);
  const damage = currentScenario(data, assumption);

  return (
    <section className="page">
      <PageHeader
        title="Nairobi Urban Flood Risk"
        subtitle="Synthetic portfolio · Susceptibility proxy"
      />
      <EventSelector data={data} tier={tier} assumption={assumption} />
      <div className="kpi-row">
        <article className="kpi">
          <span>Portfolio TIV</span>
          <strong>{kesCompact(stats.tiv_kes)}</strong>
          <em>{kesExact(stats.tiv_kes, 0)}</em>
        </article>
        <article className="kpi">
          <span>Estimated loss</span>
          <strong>{kesCompact(stats.portfolio_loss_kes)}</strong>
          <em>{kesExact(stats.portfolio_loss_kes)}</em>
        </article>
        <article className="kpi">
          <span>Loss ratio</span>
          <strong>{pct(stats.loss_pct_portfolio, 4)}</strong>
          <em>
            {event.assumed_return_period_years}Y · {damage.short_label}
          </em>
        </article>
      </div>
      <div className="panel">
        <h2>Estimated portfolio loss by assumed return period</h2>
        <LossCurve data={data} assumption={assumption} tier={tier} />
      </div>
    </section>
  );
}
