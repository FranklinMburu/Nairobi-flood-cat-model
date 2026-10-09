import { PageHeader } from "./PageHeader";
import { classLabel, kesCompact, kesExact, tierStats } from "../format";

export function Exposure({ data, assumption, tier }) {
  const stats = tierStats(data, assumption, tier);
  const classes = data.class_summary[assumption][tier];
  const largest = [...data.buildings].sort((a, b) => b.tiv_kes - a.tiv_kes).slice(0, 8);

  return (
    <section className="page">
      <PageHeader title="Exposure" subtitle="Synthetic buildings. Values are the supplied tiv_kes figures." />
      <div className="kpi-row">
        <article className="kpi">
          <span>Portfolio TIV</span>
          <strong>{kesCompact(stats.tiv_kes)}</strong>
          <em>{kesExact(stats.tiv_kes, 0)}</em>
        </article>
        <article className="kpi">
          <span>Buildings</span>
          <strong>{data.buildings.length}</strong>
          <em>All marked synthetic</em>
        </article>
      </div>
      <div className="split">
        <div className="panel">
          <h2>Housing class</h2>
          <table>
            <thead>
              <tr>
                <th>Class</th>
                <th>Buildings</th>
                <th>TIV</th>
              </tr>
            </thead>
            <tbody>
              {classes.map((row) => (
                <tr key={row.housing_class}>
                  <td>{classLabel(row.housing_class)}</td>
                  <td>{row.buildings}</td>
                  <td>{kesCompact(row.tiv_kes)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="panel">
          <h2>Largest exposures</h2>
          <table>
            <thead>
              <tr>
                <th>Location</th>
                <th>Class</th>
                <th>TIV</th>
              </tr>
            </thead>
            <tbody>
              {largest.map((building) => (
                <tr key={building.loc_id}>
                  <td>{building.loc_id}</td>
                  <td>{classLabel(building.housing_class)}</td>
                  <td>{kesCompact(building.tiv_kes)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </section>
  );
}
