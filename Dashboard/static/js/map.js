/**
 * Nepal AQ Dashboard — Leaflet map utilities
 *
 * Provides reusable functions for sensor map overlays.
 * Maps are instantiated in individual templates.
 */

// ── Map factory ───────────────────────────────────────────────────────────

/**
 * Create a base Leaflet map with OSM tiles centred on Nepal.
 *
 * @param {string} elementId  - DOM element id
 * @param {object} options    - Leaflet map options
 * @returns {L.Map}
 */
function createNepalMap(elementId, options = {}) {
  const defaults = {
    center: [28.3949, 84.1240], // Nepal centre
    zoom: 7,
    zoomControl: true,
  };
  const map = L.map(elementId, { ...defaults, ...options });

  L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
    attribution: '&copy; <a href="https://openstreetmap.org">OpenStreetMap</a> contributors',
    maxZoom: 18,
  }).addTo(map);

  return map;
}

/**
 * Add a sensor marker to a Leaflet map.
 *
 * @param {L.Map}   map
 * @param {object}  sensor   - {lat, lon, name, pm25, status, url, isIndoor}
 * @returns {L.CircleMarker}
 */
function addSensorMarker(map, sensor) {
  const isActive = sensor.status === 'ACTIVE';
  const colour = isActive
    ? (sensor.pm25 == null ? '#94a3b8'
        : sensor.pm25 <= 15 ? '#22c55e'
        : sensor.pm25 <= 40 ? '#eab308'
        : '#ef4444')
    : '#9ca3af';

  const marker = L.circleMarker([sensor.lat, sensor.lon], {
    radius: sensor.isIndoor ? 8 : 11,
    fillColor: colour,
    color: '#fff',
    weight: 2,
    opacity: 1,
    fillOpacity: isActive ? 0.85 : 0.45,
  }).addTo(map);

  const pm25Text = sensor.pm25 != null
    ? `<strong>${sensor.pm25.toFixed(1)} µg/m³</strong>`
    : 'No data';
  const placement = sensor.isIndoor ? 'Indoor' : 'Outdoor';

  marker.bindPopup(`
    <div style="font-size:13px; min-width:160px;">
      <strong style="font-size:14px;">${sensor.name}</strong><br/>
      <span style="color:#6b7280;">${placement}</span><br/>
      PM2.5: ${pm25Text}<br/>
      Status: <span style="color:${isActive ? '#16a34a' : '#dc2626'};">${sensor.status}</span>
      ${sensor.url ? `<br/><a href="${sensor.url}" style="color:#2563eb;">View details →</a>` : ''}
    </div>
  `, { maxWidth: 220 });

  return marker;
}

/**
 * Fit map bounds to a list of {lat, lon} objects.
 * Falls back to Nepal overview if fewer than 2 points.
 */
function fitMapBounds(map, points) {
  if (!points || points.length === 0) return;
  if (points.length === 1) {
    map.setView([points[0].lat, points[0].lon], 12);
    return;
  }
  const bounds = points.map(p => [p.lat, p.lon]);
  map.fitBounds(bounds, { padding: [40, 40] });
}

// Expose
window.NEPAQ_MAP = {
  createNepalMap,
  addSensorMarker,
  fitMapBounds,
};
