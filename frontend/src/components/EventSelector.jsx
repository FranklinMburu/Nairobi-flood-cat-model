import { mapController } from "../map/controller";

export function EventSelector({ data, tier, assumption }) {
  return (
    <div className="event-bar">
      <div>
        <span className="event-label">Flood event</span>
        <div className="segments">
          {data.tiers.map((item) => (
            <button
              key={item.id}
              type="button"
              className={item.id === tier ? "on" : ""}
              title={item.note}
              onClick={() => mapController.setTier(item.id)}
            >
              {item.assumed_return_period_years}Y
            </button>
          ))}
        </div>
      </div>
      <label>
        <span className="event-label">Damage assumption</span>
        <select value={assumption} onChange={(event) => mapController.setScenario(event.target.value)}>
          {data.scenarios.map((item) => (
            <option key={item.id} value={item.id}>
              {item.short_label}
            </option>
          ))}
        </select>
      </label>
      <p className="event-note">Return periods are provisional D-004 assumptions.</p>
    </div>
  );
}
