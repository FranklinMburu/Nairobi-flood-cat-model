import { useMemo, useState } from "react";
import { recordDecision } from "../../services/api";

const FIELDS = [
  ["tiv_kes_per_building", "KES value per building"],
  ["building_count", "Building count"],
  ["housing_class", "Housing class"],
  ["lat", "Latitude"],
  ["lon", "Longitude"],
];
const CLASSES = ["informal_iron_sheet", "semi_permanent", "permanent_masonry", "concrete_rcc"];

function parsed(field, raw) {
  if (field === "housing_class") return raw;
  if (raw.trim() === "") return null;
  const number = Number(raw);
  if (!Number.isFinite(number)) return raw; // the server refuses it with a clear message
  return field === "building_count" && Number.isInteger(number) ? number : number;
}

/**
 * The reviewer's decision. Every rule is enforced by the server
 * (loss_engine.approval); this form only collects the choices and shows
 * the server's answer.
 */
export function ReviewForm({ submission, user, onDecided }) {
  const items = submission.candidate ? submission.candidate.items.map((item) => item.item_id) : [];
  const [decision, setDecision] = useState("approve");
  const [reason, setReason] = useState("");
  const [acknowledged, setAcknowledged] = useState([]);
  const [excluded, setExcluded] = useState([]);
  const [corrections, setCorrections] = useState([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const required = submission.requirements.acknowledge;
  const toAcknowledge = useMemo(
    () => required.filter((key) => !excluded.includes(key.split("|")[0])),
    [required, excluded],
  );
  const unresolved = Object.entries(submission.requirements.unresolved).filter(([item]) => !excluded.includes(item));

  function toggle(list, setList, value) {
    setList(list.includes(value) ? list.filter((x) => x !== value) : [...list, value]);
  }

  function setCorrection(index, patch) {
    setCorrections(corrections.map((c, i) => (i === index ? { ...c, ...patch } : c)));
  }

  async function submit(event) {
    event.preventDefault();
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      const next = await recordDecision(submission.id, {
        decision,
        reason: reason.trim() || null,
        acknowledged: decision === "approve" ? acknowledged.filter((k) => toAcknowledge.includes(k)) : [],
        excluded_items: decision === "approve" ? excluded : [],
        corrections:
          decision === "approve"
            ? corrections.map((c) => ({ item_id: c.item_id, field: c.field, value: parsed(c.field, c.value), reason: c.reason }))
            : [],
      });
      onDecided(next);
    } catch (reason) {
      setError(reason.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="panel wf-review" onSubmit={submit}>
      <h2>Your decision</h2>
      <p className="fine">Recorded as {user.name} &lt;{user.email}&gt;. The approval applies only to this exact candidate and report.</p>
      <div className="segments" role="radiogroup" aria-label="Decision">
        {[["approve", "Approve"], ["request_review", "Request review"], ["reject", "Reject"]].map(([value, label]) => (
          <button key={value} type="button" className={decision === value ? "on" : ""} onClick={() => setDecision(value)}>
            {label}
          </button>
        ))}
      </div>

      {decision === "approve" ? (
        <>
          <h3>Exclude items</h3>
          {items.map((item) => (
            <label key={item} className="wf-check">
              <input type="checkbox" checked={excluded.includes(item)} onChange={() => toggle(excluded, setExcluded, item)} />
              Exclude {item}
            </label>
          ))}

          <h3>Acknowledge every review flag</h3>
          {toAcknowledge.length === 0 ? <p className="fine">No flags to acknowledge.</p> : null}
          {toAcknowledge.map((key) => (
            <label key={key} className="wf-check">
              <input type="checkbox" checked={acknowledged.includes(key)} onChange={() => toggle(acknowledged, setAcknowledged, key)} />
              {key.replaceAll("|", " · ")}
            </label>
          ))}

          {unresolved.length ? (
            <p className="wf-note">
              Still unresolved: {unresolved.map(([item, fields]) => `${item} (${fields.join(", ")})`).join("; ")}. Enter a
              value as a correction or exclude the item. A total for several buildings is never split automatically.
            </p>
          ) : null}

          <h3>Corrections (entered by you, with a reason)</h3>
          {corrections.map((c, index) => (
            <div key={index} className="wf-correction">
              <select value={c.item_id} onChange={(e) => setCorrection(index, { item_id: e.target.value })}>
                {items.filter((item) => !excluded.includes(item)).map((item) => <option key={item}>{item}</option>)}
              </select>
              <select value={c.field} onChange={(e) => setCorrection(index, { field: e.target.value, value: "" })}>
                {FIELDS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
              </select>
              {c.field === "housing_class" ? (
                <select value={c.value} onChange={(e) => setCorrection(index, { value: e.target.value })}>
                  <option value="">Choose…</option>
                  {CLASSES.map((cls) => <option key={cls}>{cls}</option>)}
                </select>
              ) : (
                <input value={c.value} inputMode="decimal" placeholder="Value" onChange={(e) => setCorrection(index, { value: e.target.value })} />
              )}
              <input value={c.reason} placeholder="Reason (required)" onChange={(e) => setCorrection(index, { reason: e.target.value })} />
              <button type="button" className="text-button" onClick={() => setCorrections(corrections.filter((_, i) => i !== index))}>Remove</button>
            </div>
          ))}
          <button
            type="button"
            className="text-button"
            disabled={!items.length}
            onClick={() => setCorrections([...corrections, { item_id: items.find((i) => !excluded.includes(i)) || items[0], field: FIELDS[0][0], value: "", reason: "" }])}
          >
            Add a correction
          </button>
        </>
      ) : null}

      <label className="wf-new">
        Reason {decision === "approve" ? "(optional)" : "(required)"}
        <textarea rows={2} value={reason} onChange={(e) => setReason(e.target.value)} />
      </label>
      {error ? <p className="auth-error" role="alert">{error}</p> : null}
      <button type="submit" className="send" disabled={busy}>
        {busy ? "Recording…" : "Record decision"}
      </button>
    </form>
  );
}
