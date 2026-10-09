/** Imperative map and selection control. UI actions and the copilot both call this. */
export const mapController = {
  _map: null,
  _handlers: {},

  bindMap(map) {
    this._map = map;
  },

  unbindMap(map) {
    if (this._map === map) this._map = null;
  },

  setHandlers(handlers) {
    this._handlers = handlers;
  },

  flyTo(lat, lon, zoom = 14) {
    this._map?.flyTo([lat, lon], zoom, { duration: 0.8 });
  },

  zoomIn() {
    this._map?.zoomIn();
  },

  zoomOut() {
    this._map?.zoomOut();
  },

  setTier(tier) {
    this._handlers.setTier?.(tier);
  },

  setScenario(scenario) {
    this._handlers.setScenario?.(scenario);
  },

  selectBuilding(locId) {
    this._handlers.selectBuilding?.(locId);
  },

  highlightBuildings(ids) {
    this._handlers.highlightBuildings?.(ids);
  },
};

window.MapController = mapController;
