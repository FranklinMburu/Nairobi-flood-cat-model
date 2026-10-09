import { mapController } from "../map/controller";
import { kesExact } from "../format";

export function LossCurve({ data, assumption, tier, tall = false }) {
  const points = data.tiers.map((item) => ({
    ...item,
    loss: data.tier_summary[assumption][item.id].portfolio_loss_kes,
  }));
  const width = 720;
  const height = tall ? 280 : 150;
  const pad = { l: 52, r: 16, t: 16, b: 28 };
  const max = Math.max(...points.map((point) => point.loss), 1);
  const innerW = width - pad.l - pad.r;
  const innerH = height - pad.t - pad.b;
  const x = (index) => pad.l + (index * innerW) / (points.length - 1);
  const y = (loss) => pad.t + innerH - (loss / max) * innerH;
  const line = points
    .map((point, index) => `${index ? "L" : "M"}${x(index).toFixed(1)},${y(point.loss).toFixed(1)}`)
    .join(" ");

  return (
    <div className="curve">
      <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label="Portfolio loss at five assumed return periods">
        {[0, max / 2, max].map((value) => {
          const yy = y(value);
          return (
            <g key={value}>
              <line x1={pad.l} y1={yy} x2={width - pad.r} y2={yy} stroke="#E5E7EB" />
              <text x={pad.l - 8} y={yy + 4} textAnchor="end" fill="#64748B" fontSize="11">
                {(value / 1e9).toFixed(1)}B
              </text>
            </g>
          );
        })}
        <path d={line} fill="none" stroke="#003B70" strokeWidth="2" />
        {points.map((point, index) => (
          <circle
            key={point.id}
            cx={x(index)}
            cy={y(point.loss)}
            r={point.id === tier ? 6 : 4}
            fill={point.id === tier ? "#C8102E" : "#003B70"}
          />
        ))}
      </svg>
      <div className="curve-points">
        {points.map((point) => (
          <button
            key={point.id}
            type="button"
            className={point.id === tier ? "point on" : "point"}
            onClick={() => mapController.setTier(point.id)}
          >
            <span>{point.assumed_return_period_years}Y</span>
            <strong>{kesExact(point.loss)}</strong>
          </button>
        ))}
      </div>
    </div>
  );
}
