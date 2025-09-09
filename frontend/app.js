const $ = (s) => document.querySelector(s);

/* ---- Color palette ---- */
const COLORS = {
  moisture: { line: "#60A5FA", fill: "rgba(96,165,250,0.2)" },  // light blue
  lux:      { line: "#F59E0B", fill: "rgba(245,158,11,0.2)" },   // orange-yellow
  rh:       { line: "#0D9488", fill: "rgba(13,148,136,0.2)" },   // dark turquoise
  temp:     { line: "#F97316", fill: "rgba(249,115,22,0.2)" },   // red-orange
};

let charts = { moisture: null, lux: null, climate: null };
let pollTimer = null;

// Keep last server ts per metric for delta fetches
let lastTs = { moisture_pct: null, lux: null, rh: null, temp_c: null };

// AbortControllers: one for a full reload, one for periodic tick
let loadCtrl = null;
let tickCtrl = null;

function apiBase() {
  return `${location.origin}`; // served from same host as API
}

async function fetchJSON(path, opts = {}) {
  const url = path.startsWith("http") ? path : `${apiBase()}${path}`;
  const r = await fetch(url, opts);
  if (!r.ok) throw new Error(`HTTP ${r.status}: ${await r.text()}`);
  return r.json();
}

/* ---------- Data loading ---------- */

async function loadProbes() {
  const probes = await fetchJSON("/api/probes");
  const sel = $("#probeSelect");
  sel.innerHTML = "";
  probes.forEach((p) => {
    const opt = document.createElement("option");
    opt.value = p.id;
    opt.textContent = p.label || `Probe ${p.id}`;
    sel.appendChild(opt);
  });
  if (probes.length === 0) {
    sel.innerHTML = '<option value="">No probes</option>';
  }
}

// Server returns "YYYY-MM-DD HH:mm:ss" (UTC). Normalize to ISO UTC for date adapter.
function toPoints(resp) {
  return resp.series.map((p) => ({
    x: p.ts.replace(" ", "T") + "Z",
    y: Number(p.value),
    _rawTs: p.ts,
  }));
}

async function fetchMetric(probeId, metric, hours, ctrl) {
  const after = lastTs[metric];
  const params = new URLSearchParams({ probe_id: String(probeId), metric, since_hours: String(hours) });
  if (after) params.set("after_ts", after);
  const resp = await fetchJSON(`/api/series?${params.toString()}`, ctrl ? { signal: ctrl.signal } : {});
  const pts = toPoints(resp);
  if (pts.length) lastTs[metric] = pts[pts.length - 1]._rawTs;
  return pts;
}

/* ---------- Chart helpers ---------- */

function destroyIf(chart) { if (chart) chart.destroy(); }

function makeLineChart(canvas, label, points, color, extraOpts = {}) {
  return new Chart(canvas, {
    type: "line",
    data: {
      datasets: [{
        label,
        data: points,
        borderColor: color.line,
        backgroundColor: color.fill,
        borderWidth: 2,
        pointRadius: 0,
        tension: 0.25,
        fill: false,
        spanGaps: true,
      }],
    },
    options: {
      scales: { x: { type: "time", time: { tooltipFormat: "MMM d, HH:mm" } } },
      animation: false,
      interaction: { mode: "nearest", intersect: false },
      maintainAspectRatio: false,
      ...extraOpts,
    },
  });
}

function appendPoints(chart, addPts) {
  if (!chart || !addPts.length) return;
  const ds = chart.data.datasets[0];
  ds.data.push(...addPts);       // server already sorted ASC
  chart.update("none");
}

/* ---------- Full reload / incremental tick ---------- */

async function fullReloadCharts() {
  const probeId = $("#probeSelect")?.value;
  const hours = $("#sinceHours")?.value || 24;
  if (!probeId) return;

  // Abort any in-flight full reload
  if (loadCtrl) loadCtrl.abort();
  loadCtrl = new AbortController();

  // Reset delta tracking
  lastTs = { moisture_pct: null, lux: null, rh: null, temp_c: null };

  try {
    const [mPts, lPts, rPts, tPts] = await Promise.all([
      fetchMetric(probeId, "moisture_pct", hours, loadCtrl),
      fetchMetric(probeId, "lux", hours, loadCtrl),
      fetchMetric(probeId, "rh", hours, loadCtrl),
      fetchMetric(probeId, "temp_c", hours, loadCtrl),
    ]);

    destroyIf(charts.moisture);
    charts.moisture = makeLineChart($("#moistureChart"), "Moisture %", mPts, COLORS.moisture);

    destroyIf(charts.lux);
    charts.lux = makeLineChart($("#luxChart"), "Lux", lPts, COLORS.lux);

    destroyIf(charts.climate);
    charts.climate = new Chart($("#climateChart"), {
      type: "line",
      data: {
        datasets: [
          {
            label: "RH %",
            data: rPts,
            yAxisID: "y1",
            borderColor: COLORS.rh.line,
            backgroundColor: COLORS.rh.fill,
            borderWidth: 2,
            pointRadius: 0,
            tension: 0.25,
            fill: false,
            spanGaps: true,
          },
          {
            label: "Temp °C",
            data: tPts,
            yAxisID: "y2",
            borderColor: COLORS.temp.line,
            backgroundColor: COLORS.temp.fill,
            borderWidth: 2,
            pointRadius: 0,
            tension: 0.25,
            fill: false,
            spanGaps: true,
          }
        ]
      },
      options: {
        scales: {
          x: { type: "time", time: { tooltipFormat: "MMM d, HH:mm" } },
          y1: { position: "left" },
          y2: { position: "right" }
        },
        animation: false,
        interaction: { mode: "nearest", intersect: false },
        maintainAspectRatio: false
      }
    });
  } catch (e) {
    if (e.name !== "AbortError") console.warn("fullReloadCharts error:", e);
  }
}

async function incrementalTick() {
  const probeId = $("#probeSelect")?.value;
  const hours = $("#sinceHours")?.value || 24;
  if (!probeId) return;

  // Abort any previous tick so ticks don't overlap
  if (tickCtrl) tickCtrl.abort();
  tickCtrl = new AbortController();

  try {
    const [mPts, lPts, rPts, tPts] = await Promise.all([
      fetchMetric(probeId, "moisture_pct", hours, tickCtrl),
      fetchMetric(probeId, "lux", hours, tickCtrl),
      fetchMetric(probeId, "rh", hours, tickCtrl),
      fetchMetric(probeId, "temp_c", hours, tickCtrl),
    ]);

    appendPoints(charts.moisture, mPts);
    appendPoints(charts.lux, lPts);

    if (charts.climate) {
      if (rPts.length) charts.climate.data.datasets[0].data.push(...rPts);
      if (tPts.length) charts.climate.data.datasets[1].data.push(...tPts);
      if (rPts.length || tPts.length) charts.climate.update("none");
    }
  } catch (e) {
    if (e.name !== "AbortError") console.warn("incrementalTick error:", e);
  }
}

/* ---------- Polling & UI wiring ---------- */

function startPolling() {
  stopPolling();
  const base = Math.max(2, Math.min(120, Number($("#periodSec")?.value || 5)));
  pollTimer = setInterval(() => {
    if (document.hidden || !$("#autoRefresh")?.checked) return;
    incrementalTick();
  }, base * 1000);
}

function stopPolling() {
  if (pollTimer) clearInterval(pollTimer);
  pollTimer = null;
}

function wireUI() {
  $("#refreshBtn")?.addEventListener("click", fullReloadCharts);
  $("#probeSelect")?.addEventListener("change", async () => { await fullReloadCharts(); });
  $("#sinceHours")?.addEventListener("change", async () => { await fullReloadCharts(); });

  $("#periodSec")?.addEventListener("change", startPolling);
  $("#autoRefresh")?.addEventListener("change", startPolling);

  document.addEventListener("visibilitychange", () => {
    if (!document.hidden && $("#autoRefresh")?.checked) incrementalTick();
  });
}

/* ---------- Bootstrap ---------- */

(async function init() {
  wireUI();
  await loadProbes();
  await fullReloadCharts();
  startPolling();
})();
