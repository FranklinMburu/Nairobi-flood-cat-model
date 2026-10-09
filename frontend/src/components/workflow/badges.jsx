const STATUS_TEXT = {
  awaiting_approval: "Awaiting approval",
  review_requested: "Review requested",
  rejected_by_reviewer: "Rejected by reviewer",
  approved: "Approved, not yet run",
  stale_approval: "Approval is stale",
  completed: "Model run completed",
  failed: "Extraction failed",
  rejected: "Rejected by verifier",
  integrity_error: "Integrity error",
  unreadable: "Unreadable record",
};

const STATUS_TONE = {
  completed: "ok",
  approved: "ok",
  awaiting_approval: "wait",
  review_requested: "wait",
  stale_approval: "bad",
  rejected_by_reviewer: "bad",
  failed: "bad",
  rejected: "bad",
  integrity_error: "bad",
  unreadable: "bad",
};

export function StatusBadge({ status }) {
  return <span className={`badge ${STATUS_TONE[status] || "wait"}`}>{STATUS_TEXT[status] || status}</span>;
}

/** Replay responses are never presented as live AI output. */
export function ModeBadge({ mode }) {
  if (mode === "replay") return <span className="badge replay" title="A recorded response; no AI was called">Replay</span>;
  if (mode === "live") return <span className="badge live">Live AI</span>;
  return <span className="badge">{mode}</span>;
}

export function ResultBadge({ result }) {
  const tone = result === "pass" ? "ok" : result === "review" ? "wait" : "bad";
  return <span className={`badge ${tone}`}>{result}</span>;
}

export function Hash({ value }) {
  if (!value) return <code>—</code>;
  return <code title={value}>{value.slice(0, 16)}…</code>;
}
