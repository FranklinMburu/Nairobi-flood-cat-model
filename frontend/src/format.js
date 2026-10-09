/** Display helpers. Loss figures are read from the API; nothing here prices risk. */

export const CLASS_LABELS = {
  informal_iron_sheet: "Informal iron sheet",
  semi_permanent: "Semi-permanent",
  permanent_masonry: "Permanent masonry",
  concrete_rcc: "Concrete RCC",
};

export function classLabel(value) {
  return CLASS_LABELS[value] || value;
}

export function kesExact(value, digits = 2) {
  return `KES ${Number(value).toLocaleString("en-US", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })}`;
}

export function kesCompact(value) {
  const number = Number(value);
  const abs = Math.abs(number);
  if (abs >= 1e9) return `KES ${(number / 1e9).toFixed(3)}B`;
  if (abs >= 1e6) return `KES ${(number / 1e6).toFixed(2)}M`;
  if (abs >= 1e3) return `KES ${(number / 1e3).toFixed(1)}K`;
  return kesExact(number, 0);
}

/** Specification 3.2 stores ratios as fractions. The ×100 is display only. */
export function pct(fraction, digits) {
  return `${(Number(fraction) * 100).toLocaleString("en-US", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })}%`;
}

export function currentTier(data, tierId) {
  return data.tiers.find((tier) => tier.id === tierId);
}

export function currentScenario(data, scenarioId) {
  return data.scenarios.find((scenario) => scenario.id === scenarioId);
}

export function tierStats(data, scenarioId, tierId) {
  return data.tier_summary[scenarioId][tierId];
}

export function metricOf(building, scenarioId, tierId) {
  return building.metrics[scenarioId][tierId];
}
