import { useEffect, useMemo, useRef, useState } from "react";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import { BuildingRiskCard } from "./BuildingRiskCard";
import { EventSelector } from "./EventSelector";
import { metricOf } from "../format";
import { mapController } from "../map/controller";

const COLORS = { none: "#94A3B8", low: "#0F766E", mid: "#D97706", high: "#EA580C", severe: "#C8102E" };
const LEVELS = [
  ["none", "Not flagged"],
  ["low", "Low"],
  ["mid", "Moderate"],
  ["high", "High"],
  ["severe", "Severe"],
];

const BASEMAPS = {
  street: {
    url: "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
    attribution: "&copy; OpenStreetMap",
  },
  satellite: {
    url: "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
    attribution: "Tiles &copy; Esri",
  },
};

function displayCuts(losses) {
  const positive = losses.filter((value) => value > 0).sort((a, b) => a - b);
  if (!positive.length) return [Infinity, Infinity, Infinity];
  const at = (share) => positive[Math.min(positive.length - 1, Math.floor(share * positive.length))];
  return [at(0.25), at(0.5), at(0.75)];
}

function bandFor(loss, cuts) {
  if (!(loss > 0)) return "none";
  if (loss <= cuts[0]) return "low";
  if (loss <= cuts[1]) return "mid";
  if (loss <= cuts[2]) return "high";
  return "severe";
}

export function RiskMap({ data, tier, assumption, selectedId, highlighted, visible }) {
  const mapNode = useRef(null);
  const mapRef = useRef(null);
  const markers = useRef(new Map());
  const [query, setQuery] = useState("");
  const [openSearch, setOpenSearch] = useState(false);
  const [basemap, setBasemap] = useState("street");
  const [band, setBand] = useState("");
  const framedBand = useRef("");
  const basemapRef = useRef(basemap);
  const baseLayers = useRef(null);
  basemapRef.current = basemap;
  const byId = useMemo(() => new Map(data.buildings.map((building) => [building.loc_id, building])), [data]);
  const tierMeta = data.tiers.find((item) => item.id === tier);
  const selected = selectedId ? byId.get(selectedId) : null;

  useEffect(() => {
    if (!mapNode.current || mapRef.current) return undefined;
    const map = L.map(mapNode.current, { zoomControl: false, minZoom: 10, maxZoom: 18 });
    const layers = {};
    for (const [name, source] of Object.entries(BASEMAPS)) {
      layers[name] = L.tileLayer(source.url, { attribution: source.attribution, maxZoom: 19 });
    }
    const initial = basemapRef.current === "satellite" ? layers.satellite : layers.street;
    initial.addTo(map);
    baseLayers.current = { ...layers, active: initial };
    for (const building of data.buildings) {
      const marker = L.circleMarker([building.lat, building.lon], {
        radius: 5,
        weight: 1,
        color: COLORS.none,
        fillColor: COLORS.none,
        fillOpacity: 0.9,
      });
      marker.on("click", (event) => {
        L.DomEvent.stopPropagation(event);
        mapController.selectBuilding(building.loc_id);
      });
      marker.addTo(map);
      markers.current.set(building.loc_id, marker);
    }
    map.fitBounds(data.buildings.map((building) => [building.lat, building.lon]), { padding: [28, 28] });
    mapRef.current = map;
    mapController.bindMap(map);
    return () => {
      mapController.unbindMap(map);
      map.remove();
      mapRef.current = null;
      markers.current.clear();
      baseLayers.current = null;
    };
  }, [data]);

  useEffect(() => {
    const map = mapRef.current;
    const layers = baseLayers.current;
    if (!map || !layers) return;
    const next = basemap === "satellite" ? layers.satellite : layers.street;
    if (next === layers.active) return;
    map.removeLayer(layers.active);
    next.addTo(map);
    layers.active = next;
  }, [basemap]);

  useEffect(() => {
    if (!visible || !mapRef.current) return;
    mapRef.current.invalidateSize();
  }, [visible]);

  useEffect(() => {
    if (!mapRef.current || !selectedId) return;
    const building = byId.get(selectedId);
    if (!building) return;
    const zoom = mapRef.current.getZoom() < 13 ? 14 : mapRef.current.getZoom();
    mapController.flyTo(building.lat, building.lon, zoom);
  }, [selectedId, byId]);

  useEffect(() => {
    if (!mapRef.current || !highlighted.length || band) return;
    const points = highlighted.map((id) => byId.get(id)).filter(Boolean);
    if (points.length === 1) mapController.flyTo(points[0].lat, points[0].lon, 15);
    else mapRef.current.fitBounds(points.map((building) => [building.lat, building.lon]), { padding: [48, 48] });
  }, [highlighted, byId, band]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    const losses = data.buildings.map((building) => metricOf(building, assumption, tier).loss_kes);
    const cuts = displayCuts(losses);
    const highlightSet = new Set(highlighted);
    const visibleBuildings = [];
    if (band && selectedId) {
      const current = byId.get(selectedId);
      if (current && bandFor(metricOf(current, assumption, tier).loss_kes, cuts) !== band) {
        mapController.selectBuilding("");
      }
    }
    for (const building of data.buildings) {
      const loss = metricOf(building, assumption, tier).loss_kes;
      const level = bandFor(loss, cuts);
      const active = building.loc_id === selectedId || highlightSet.has(building.loc_id);
      const marker = markers.current.get(building.loc_id);
      if (!marker) continue;
      if (band && level !== band) {
        if (marker._map) map.removeLayer(marker);
        continue;
      }
      visibleBuildings.push(building);
      if (!marker._map) marker.addTo(map);
      const fill = COLORS[level];
      marker.setStyle({
        radius: active ? 8 : loss > 0 ? 5.5 : 4,
        color: active ? "#003B70" : fill,
        weight: active ? 2 : 1,
        fillColor: fill,
        fillOpacity: loss > 0 ? 0.92 : 0.55,
      });
      if (loss > 0 || active) marker.bringToFront();
    }
    const frame = `${band}|${tier}|${assumption}`;
    if (band && visibleBuildings.length > 1 && framedBand.current !== frame) {
      framedBand.current = frame;
      map.fitBounds(visibleBuildings.map((building) => [building.lat, building.lon]), { padding: [48, 48] });
    }
    if (!band) framedBand.current = "";
  }, [data, tier, assumption, selectedId, highlighted, band, byId]);

  const results = searchItems(data, query);

  return (
    <section className="map-page">
      <div className="map-toolbar">
        <EventSelector data={data} tier={tier} assumption={assumption} />
        <form
          className="search"
          onSubmit={(event) => {
            event.preventDefault();
            if (results[0]) choose(results[0]);
          }}
        >
          <input
            value={query}
            placeholder="Search hotspot or building ID"
            aria-label="Search hotspot or building ID"
            onChange={(event) => {
              setQuery(event.target.value);
              setOpenSearch(true);
            }}
            onFocus={() => setOpenSearch(true)}
          />
          {openSearch && results.length > 0 ? (
            <div className="search-results">
              {results.map((item) => (
                <button key={item.key} type="button" onClick={() => choose(item)}>
                  <span>{item.label}</span>
                  <small>{item.detail}</small>
                </button>
              ))}
            </div>
          ) : null}
        </form>
        <div className="map-controls">
          <div>
            <span className="event-label">View</span>
            <div className="segments" role="group" aria-label="Map view">
              <button
                type="button"
                className={basemap === "street" ? "on" : ""}
                aria-pressed={basemap === "street"}
                onClick={() => setBasemap("street")}
              >
                Street
              </button>
              <button
                type="button"
                className={basemap === "satellite" ? "on" : ""}
                aria-pressed={basemap === "satellite"}
                onClick={() => setBasemap("satellite")}
              >
                Satellite
              </button>
            </div>
          </div>
          <div className="zoom">
            <button type="button" aria-label="Zoom in" onClick={() => mapController.zoomIn()}>+</button>
            <button type="button" aria-label="Zoom out" onClick={() => mapController.zoomOut()}>−</button>
          </div>
        </div>
      </div>
      <div className="map-stage">
        <div ref={mapNode} className="map-canvas" />
        <div className={band ? "legend filtering" : "legend"} role="group" aria-label="Severity filter">
          {LEVELS.map(([id, label]) => (
            <button
              key={id}
              type="button"
              className={band === id ? "on" : ""}
              aria-pressed={band === id}
              onClick={() => toggleBand(id)}
            >
              <i style={{ background: COLORS[id] }} />
              {label}
            </button>
          ))}
        </div>
        {selected ? (
          <BuildingRiskCard
            building={selected}
            tier={tierMeta}
            assumption={assumption}
            onClose={() => mapController.selectBuilding("")}
          />
        ) : null}
      </div>
    </section>
  );

  function toggleBand(id) {
    setBand((current) => (current === id ? "" : id));
  }

  function choose(item) {
    setOpenSearch(false);
    setQuery(item.label);
    if (item.hotspot) mapController.flyTo(item.hotspot.lat, item.hotspot.lon, 14);
    else mapController.selectBuilding(item.building.loc_id);
  }
}

function searchItems(data, query) {
  const q = query.trim().toLowerCase();
  if (!q) return [];
  const rank = (value) => {
    const text = value.toLowerCase();
    if (text === q) return 0;
    if (text.startsWith(q)) return 1;
    return 2;
  };
  const hotspots = data.hotspots
    .filter((hotspot) => hotspot.name.toLowerCase().includes(q))
    .sort((a, b) => rank(a.name) - rank(b.name))
    .map((hotspot) => ({
      key: `h-${hotspot.name}`,
      label: hotspot.name,
      detail: "Named area",
      hotspot,
    }));
  const buildings = data.buildings
    .filter((building) => building.loc_id.toLowerCase().includes(q))
    .sort((a, b) => rank(a.loc_id) - rank(b.loc_id))
    .slice(0, 6)
    .map((building) => ({
      key: building.loc_id,
      label: building.loc_id,
      detail: "Building",
      building,
    }));
  return [...hotspots, ...buildings].slice(0, 8);
}
