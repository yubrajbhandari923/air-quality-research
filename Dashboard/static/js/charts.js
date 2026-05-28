/**
 * Nepal AQ Dashboard — Chart.js global defaults
 *
 * Sets publication-quality defaults for all charts:
 *   - Clean font styling
 *   - Consistent colour palette
 *   - WHO guideline annotation helpers
 */

// ── Global Chart.js defaults ──────────────────────────────────────────────

Chart.defaults.font.family = "'Inter', 'Helvetica Neue', Arial, sans-serif";
Chart.defaults.font.size = 11;
Chart.defaults.color = '#6b7280';
Chart.defaults.borderColor = '#e5e7eb';

// ── Nepal AQ colour palette ───────────────────────────────────────────────

const PALETTE = {
  pm25:    '#3b82f6',   // blue
  pm10:    '#8b5cf6',   // violet
  pm1:     '#06b6d4',   // cyan
  pm4:     '#0ea5e9',   // sky
  co2:     '#10b981',   // emerald
  tvoc:    '#f59e0b',   // amber
  temp:    '#ef4444',   // red
  rh:      '#6366f1',   // indigo
  baro:    '#64748b',   // slate
  indoor:  '#8b5cf6',
  outdoor: '#0d9488',

  // Guideline colours
  who:     '#6366f1',   // indigo dashed
  nepal:   '#ef4444',   // red dashed
};

// Expose to templates
window.NEPAQ_PALETTE = PALETTE;

/**
 * Create a WHO/Nepal guideline annotation dataset for inclusion in
 * any Chart.js time-series datasets array.
 *
 * @param {Array}  labels   - Array of Date objects (x-axis labels)
 * @param {number} whoValue - WHO 24h PM2.5 guideline (15 µg/m³)
 * @param {number} nepalValue - Nepal NAAQS 24h (40 µg/m³)
 * @returns {Array} Two dataset objects to append to your chart datasets
 */
function buildGuidelineDatasets(labels, whoValue = 15, nepalValue = 40) {
  return [
    {
      label: `WHO 24h (${whoValue} µg/m³)`,
      data: labels.map(() => whoValue),
      borderColor: PALETTE.who,
      borderDash: [6, 4],
      borderWidth: 1.5,
      pointRadius: 0,
      fill: false,
      tension: 0,
      order: 0,
    },
    {
      label: `Nepal NAAQS (${nepalValue} µg/m³)`,
      data: labels.map(() => nepalValue),
      borderColor: PALETTE.nepal,
      borderDash: [6, 4],
      borderWidth: 1.5,
      pointRadius: 0,
      fill: false,
      tension: 0,
      order: 0,
    },
  ];
}

/**
 * Get the CSS colour class for a PM2.5 value.
 * @param {number|null} value
 * @returns {string} Tailwind text-color class
 */
function pm25ColourClass(value) {
  if (value == null) return 'text-gray-400';
  if (value <= 12) return 'text-green-600';
  if (value <= 35.4) return 'text-yellow-500';
  if (value <= 55.4) return 'text-orange-500';
  if (value <= 150.4) return 'text-red-600';
  if (value <= 250.4) return 'text-purple-700';
  return 'text-red-900';
}

/**
 * Colour bar segments green/yellow/red based on completeness %.
 * @param {number} pct  0–100
 * @returns {string} hex colour
 */
function completenessColour(pct) {
  if (pct >= 80) return '#22c55e';
  if (pct >= 50) return '#eab308';
  return '#ef4444';
}

// ── Utility: ISO string to Nepal local time label ─────────────────────────

function toNepalLabel(isoString) {
  const d = new Date(isoString);
  return d.toLocaleString('en-US', {
    timeZone: 'Asia/Kathmandu',
    month: 'short', day: 'numeric',
    hour: '2-digit', minute: '2-digit',
    hour12: false,
  });
}

// Expose helpers globally
window.NEPAQ = {
  palette: PALETTE,
  buildGuidelineDatasets,
  pm25ColourClass,
  completenessColour,
  toNepalLabel,
};
