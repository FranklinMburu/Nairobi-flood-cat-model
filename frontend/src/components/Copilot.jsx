import { useState } from "react";
import { classLabel, currentScenario, currentTier, kesExact, metricOf, pct, tierStats } from "../format";
import { mapController } from "../map/controller";

const SECTION_LABEL = {
  overview: "Overview",
  map: "Risk Map",
  exposure: "Exposure",
  loss: "Loss Analysis",
};

const PROMPTS = {
  overview: ["What is the portfolio loss?", "What is the 250-year loss?", "Show highest-risk buildings"],
  map: ["Zoom into Westlands", "Show highest-risk buildings", "Explain this building's risk"],
  exposure: ["What is the portfolio TIV?", "How many buildings?", "What is the largest exposure?"],
  loss: ["What is the portfolio loss?", "What is the 100-year loss?", "What is the 250-year loss?"],
};

export function Copilot({ section, hidden, data, tier, assumption, selected, onOpenMap }) {
  const [open, setOpen] = useState(false);
  const [messages, setMessages] = useState([
    { role: "bot", text: "Commands use the map controller and the engine results. A language model is not connected yet." },
  ]);
  const [draft, setDraft] = useState("");
  const [listening, setListening] = useState(false);
  const prompts = PROMPTS[section] || PROMPTS.overview;
  const event = currentTier(data, tier);
  const damage = currentScenario(data, assumption);

  function push(role, text) {
    setMessages((current) => [...current, { role, text }]);
  }

  function ask(raw) {
    const text = raw.trim();
    if (!text) return;
    setDraft("");
    push("user", text);
    const q = text.toLowerCase();

    if (q.includes("highest")) {
      const ranked = data.buildings
        .map((building) => ({ id: building.loc_id, loss: metricOf(building, assumption, tier).loss_kes }))
        .sort((a, b) => b.loss - a.loss)
        .slice(0, 10);
      onOpenMap();
      mapController.highlightBuildings(ranked.map((item) => item.id));
      push("bot", `Highlighted 10 buildings with the largest modelled loss. Largest is ${ranked[0].id} at ${kesExact(ranked[0].loss)}.`);
      return;
    }
    if (q.includes("250")) {
      mapController.setTier("common");
      const loss = data.tier_summary[assumption].common.portfolio_loss_kes;
      push("bot", `Common is the assumed 250-year point (D-004). Estimated portfolio loss is ${kesExact(loss)}.`);
      return;
    }
    if (q.includes("100")) {
      mapController.setTier("occasional");
      const loss = data.tier_summary[assumption].occasional.portfolio_loss_kes;
      push("bot", `Occasional is the assumed 100-year point (D-004). Estimated portfolio loss is ${kesExact(loss)}.`);
      return;
    }
    if (q.includes("portfolio loss") || q.includes("what is the loss")) {
      const stats = tierStats(data, assumption, tier);
      push("bot", `Estimated portfolio loss at the ${event.assumed_return_period_years}-year event is ${kesExact(stats.portfolio_loss_kes)}, ${pct(stats.loss_pct_portfolio, 4)} of supplied TIV.`);
      return;
    }
    if (q.includes("explain")) {
      if (!selected) {
        push("bot", "Select a building on the risk map first.");
        return;
      }
      const metric = metricOf(selected, assumption, event.id);
      onOpenMap();
      mapController.selectBuilding(selected.loc_id);
      push(
        "bot",
        `${selected.loc_id} is ${classLabel(selected.housing_class)}. Supplied TIV is ${kesExact(selected.tiv_kes, 0)}. At ${event.label}, susceptibility is ${Number(metric.hazard_score).toFixed(4)}, damage ratio ${pct(metric.damage_ratio, 2)}, estimated loss ${kesExact(metric.loss_kes)}. ${metric.affected ? "The proxy flags this building." : "The proxy does not flag this building."}`,
      );
      return;
    }
    if (q.includes("housing")) {
      const classes = data.class_summary[assumption][tier]
        .map((row) => `${classLabel(row.housing_class)} ${row.buildings}`)
        .join(", ");
      push("bot", `Housing classes: ${classes}.`);
      return;
    }
    if (q.includes("how many") || q.includes("number of buildings")) {
      push("bot", `The portfolio contains ${data.buildings.length} synthetic buildings.`);
      return;
    }
    if (q.includes("largest")) {
      const top = [...data.buildings].sort((a, b) => b.tiv_kes - a.tiv_kes)[0];
      push("bot", `Largest supplied exposure is ${top.loc_id}, ${classLabel(top.housing_class)}, ${kesExact(top.tiv_kes, 0)}.`);
      return;
    }
    if (q.includes("tiv")) {
      const stats = tierStats(data, assumption, tier);
      push("bot", `Portfolio TIV is ${kesExact(stats.tiv_kes, 0)}, taken from the supplied exposure.`);
      return;
    }
    const building = data.buildings.find((item) => item.loc_id.toLowerCase() === q);
    if (building) {
      onOpenMap();
      mapController.selectBuilding(building.loc_id);
      push("bot", `Selected ${building.loc_id}.`);
      return;
    }
    const hotspot = data.hotspots.find((item) => q.includes(item.name.toLowerCase()));
    if (hotspot) {
      onOpenMap();
      mapController.flyTo(hotspot.lat, hotspot.lon, 14);
      push("bot", `Moved the map to ${hotspot.name}.`);
      return;
    }
    push("bot", "Try a suggested question, a hotspot name, or a building ID such as NBO-0002.");
  }

  function listen() {
    const Speech = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!Speech) {
      push("bot", "Voice input is not available in this browser.");
      return;
    }
    const recognition = new Speech();
    recognition.lang = "en-US";
    recognition.onstart = () => setListening(true);
    recognition.onend = () => setListening(false);
    recognition.onerror = () => {
      setListening(false);
      push("bot", "The microphone could not be used.");
    };
    recognition.onresult = (eventResult) => ask(eventResult.results[0][0].transcript);
    recognition.start();
  }

  if (hidden) return null;

  if (!open) {
    return (
      <button type="button" className={section === "map" ? "copilot-toggle on-map" : "copilot-toggle"} onClick={() => setOpen(true)}>
        CAT Copilot
      </button>
    );
  }

  return (
    <section className={section === "map" ? "copilot-panel on-map" : "copilot-panel"} aria-label="CAT Copilot">
      <header className="copilot-head">
        <div>
          <strong>CAT Copilot</strong>
          <p>
            {SECTION_LABEL[section] || "Portfolio"} · {event.assumed_return_period_years}Y · {damage.short_label}
          </p>
        </div>
        <button type="button" className="copilot-hide" onClick={() => setOpen(false)}>
          Hide
        </button>
      </header>
      <div className="transcript">
        {messages.map((message, index) => (
          <p key={index} className={message.role === "user" ? "msg user" : "msg"}>
            {message.text}
          </p>
        ))}
      </div>
      <div className="prompts">
        {prompts.map((prompt) => (
          <button key={prompt} type="button" onClick={() => ask(prompt)}>
            {prompt}
          </button>
        ))}
      </div>
      <form
        className="composer"
        onSubmit={(eventForm) => {
          eventForm.preventDefault();
          ask(draft);
        }}
      >
        <input
          value={draft}
          placeholder="Ask about this risk..."
          aria-label="Ask about this risk"
          onChange={(eventForm) => setDraft(eventForm.target.value)}
        />
        <button type="button" className={listening ? "mic on" : "mic"} onClick={listen}>
          Mic
        </button>
        <button type="submit" className="send">
          Send
        </button>
      </form>
    </section>
  );
}
