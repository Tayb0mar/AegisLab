/* AegisLab dashboard client.
 *
 * Safety rules applied here:
 *  - every dynamic string is inserted with textContent (never innerHTML);
 *  - a failed API call switches the UI to a visible degraded state instead of
 *    silently keeping stale numbers;
 *  - stale/offline readings are dimmed and labelled, never shown as live.
 */

"use strict";

const API_BASE = ""; // same origin: the FastAPI app serves this page
const POLL_INTERVAL_MS = 3000;
const HISTORY_LIMIT = 50;
const ALERTS_LIMIT = 20;
const EVENTS_LIMIT = 20;

const el = {
  simBadge: document.getElementById("sim-badge"),
  deviceBadge: document.getElementById("device-badge"),
  apiErrorBanner: document.getElementById("api-error-banner"),
  degradedBanner: document.getElementById("degraded-banner"),
  appStatus: document.getElementById("app-status"),
  deviceStatus: document.getElementById("device-status"),
  lastReading: document.getElementById("last-reading"),
  dataSource: document.getElementById("data-source"),
  temperature: document.getElementById("temperature-value"),
  humidity: document.getElementById("humidity-value"),
  light: document.getElementById("light-value"),
  motion: document.getElementById("motion-value"),
  cards: Array.from(document.querySelectorAll(".card")),
  historyTableBody: document.querySelector("#history-table tbody"),
  historyChart: document.getElementById("history-chart"),
  alertsList: document.getElementById("alerts-list"),
  eventsList: document.getElementById("events-list"),
};

async function fetchJson(path, options) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 5000);
  try {
    const response = await fetch(API_BASE + path, {
      ...options,
      signal: controller.signal,
    });
    const body = await response.json().catch(() => null);
    return { ok: response.ok, status: response.status, body };
  } finally {
    clearTimeout(timer);
  }
}

function setBadge(state) {
  const badge = el.deviceBadge;
  badge.className = "badge";
  if (state === "online") {
    badge.classList.add("badge-online");
    badge.textContent = "DEVICE ONLINE";
  } else if (state === "sensor_error") {
    badge.classList.add("badge-stale");
    badge.textContent = "SENSOR ERROR";
  } else if (state === "stale") {
    badge.classList.add("badge-stale");
    badge.textContent = "DEVICE STALE";
  } else if (state === "offline") {
    badge.classList.add("badge-offline");
    badge.textContent = "DEVICE OFFLINE";
  } else if (state === "starting") {
    badge.classList.add("badge-unknown");
    badge.textContent = "STARTING…";
  } else {
    badge.classList.add("badge-unknown");
    badge.textContent = "UNKNOWN";
  }
}

function markCardsStale(stale) {
  el.cards.forEach((card) => card.classList.toggle("offline", stale));
}

function formatTimestamp(iso) {
  if (!iso) return "never";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleString();
}

function showApiFailure() {
  el.apiErrorBanner.classList.remove("hidden");
  setBadge("unknown");
  markCardsStale(true);
  el.appStatus.textContent = "unreachable";
}

/* ------------------------------ status ---------------------------------- */

async function refreshStatus() {
  const { ok, body } = await fetchJson("/api/status");
  if (!ok || !body) throw new Error("status failed");

  el.apiErrorBanner.classList.add("hidden");
  el.appStatus.textContent = body.application;
  el.degradedBanner.classList.toggle("hidden", body.application !== "degraded");

  el.deviceStatus.textContent = body.device;
  setBadge(body.device);
  el.lastReading.textContent = formatTimestamp(body.last_reading_at);
  el.dataSource.textContent =
    body.data_source === "simulated" ? "simulator (mock)" :
    body.data_source === "device" ? "Arduino (serial)" : "none";
  el.simBadge.classList.toggle("hidden", body.data_source !== "simulated");
}

/* ------------------------------ latest ---------------------------------- */

async function refreshLatest() {
  const { ok, status, body } = await fetchJson("/api/readings/latest");
  if (!ok) {
    if (status === 404) {
      // No data yet: keep placeholders, do not fabricate values.
      el.temperature.textContent = "—";
      el.humidity.textContent = "—";
      el.light.textContent = "—";
      el.motion.textContent = "no data";
      markCardsStale(true);
      return;
    }
    throw new Error("latest failed");
  }

  el.temperature.textContent =
    body.temperature === null ? "n/a" : body.temperature.toFixed(1);
  el.humidity.textContent =
    body.humidity === null ? "n/a" : body.humidity.toFixed(1);
  el.light.textContent = body.light === null ? "n/a" : String(body.light);
  el.motion.textContent = body.motion ? "DETECTED" : "none";
  markCardsStale(Boolean(body.is_stale));
}

/* ------------------------------ history --------------------------------- */

function drawChart(readings) {
  const canvas = el.historyChart;
  const ctx = canvas.getContext("2d");
  ctx.clearRect(0, 0, canvas.width, canvas.height);

  const temps = readings
    .filter((r) => r.temperature !== null)
    .map((r) => r.temperature)
    .reverse(); // API returns newest first; chart wants oldest → newest
  if (temps.length < 2) return;

  const pad = 28;
  const w = canvas.width - pad * 2;
  const h = canvas.height - pad * 2;
  const min = Math.min(...temps);
  const max = Math.max(...temps);
  const span = Math.max(max - min, 1);

  ctx.strokeStyle = "#2a3542";
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(pad, pad);
  ctx.lineTo(pad, pad + h);
  ctx.lineTo(pad + w, pad + h);
  ctx.stroke();

  ctx.fillStyle = "#8b98a5";
  ctx.font = "12px Segoe UI, sans-serif";
  ctx.fillText(max.toFixed(1) + "°C", 2, pad + 4);
  ctx.fillText(min.toFixed(1) + "°C", 2, pad + h);

  ctx.strokeStyle = "#4cc2ff";
  ctx.lineWidth = 2;
  ctx.beginPath();
  temps.forEach((t, i) => {
    const x = pad + (i / (temps.length - 1)) * w;
    const y = pad + h - ((t - min) / span) * h;
    if (i === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  });
  ctx.stroke();
}

function renderHistoryTable(readings) {
  const tbody = el.historyTableBody;
  tbody.textContent = "";
  if (readings.length === 0) {
    const row = tbody.insertRow();
    const cell = row.insertCell();
    cell.colSpan = 6;
    cell.className = "empty";
    cell.textContent = "No readings stored.";
    return;
  }
  readings.forEach((r) => {
    const row = tbody.insertRow();
    row.insertCell().textContent = formatTimestamp(r.recorded_at);
    row.insertCell().textContent =
      r.temperature === null ? "n/a" : r.temperature.toFixed(1);
    row.insertCell().textContent =
      r.humidity === null ? "n/a" : r.humidity.toFixed(1);
    row.insertCell().textContent = r.light === null ? "n/a" : String(r.light);
    row.insertCell().textContent = r.motion ? "yes" : "no";
    const sourceCell = row.insertCell();
    sourceCell.textContent = r.is_simulated ? "simulated" : "device";
    if (r.is_simulated) sourceCell.className = "sim";
  });
}

async function refreshHistory() {
  const { ok, body } = await fetchJson(`/api/readings?limit=${HISTORY_LIMIT}`);
  if (!ok || !body) throw new Error("history failed");
  renderHistoryTable(body.items);
  drawChart(body.items);
}

/* ------------------------------ alerts ---------------------------------- */

async function acknowledgeAlert(id) {
  const { ok } = await fetchJson(`/api/alerts/${id}/acknowledge`, {
    method: "PATCH",
  });
  if (ok) await refreshAlerts();
}

function renderAlerts(alerts) {
  const list = el.alertsList;
  list.textContent = "";
  if (alerts.length === 0) {
    const li = document.createElement("li");
    li.className = "empty";
    li.textContent = "No alerts.";
    list.appendChild(li);
    return;
  }
  alerts.forEach((alert) => {
    const li = document.createElement("li");
    li.className = "sev-" + alert.severity;

    const top = document.createElement("div");
    top.className = "alert-top";

    const type = document.createElement("span");
    type.className = "alert-type";
    type.textContent = alert.type;

    const sev = document.createElement("span");
    sev.className = "alert-sev";
    sev.textContent = alert.severity;

    const time = document.createElement("span");
    time.className = "alert-time";
    time.textContent = formatTimestamp(alert.created_at);

    top.append(type, sev, time);

    const message = document.createElement("p");
    message.className = "alert-message";
    message.textContent = alert.message;

    li.append(top, message);

    if (alert.acknowledged) {
      const acked = document.createElement("span");
      acked.className = "alert-acked";
      acked.textContent = "✓ acknowledged";
      li.appendChild(acked);
    } else {
      const button = document.createElement("button");
      button.className = "ack-button";
      button.type = "button";
      button.textContent = "Acknowledge";
      button.addEventListener("click", () => acknowledgeAlert(alert.id));
      li.appendChild(button);
    }
    list.appendChild(li);
  });
}

async function refreshAlerts() {
  const { ok, body } = await fetchJson(`/api/alerts?limit=${ALERTS_LIMIT}`);
  if (!ok || !body) throw new Error("alerts failed");
  renderAlerts(body.items);
}

/* ------------------------------ events ---------------------------------- */

async function refreshEvents() {
  const { ok, body } = await fetchJson(`/api/events?limit=${EVENTS_LIMIT}`);
  if (!ok || !body) throw new Error("events failed");
  const list = el.eventsList;
  list.textContent = "";
  if (body.items.length === 0) {
    const li = document.createElement("li");
    li.className = "empty";
    li.textContent = "No events.";
    list.appendChild(li);
    return;
  }
  body.items.forEach((event) => {
    const li = document.createElement("li");
    const type = document.createElement("span");
    type.className = "event-type";
    type.textContent = event.event_type;
    li.appendChild(type);
    li.appendChild(
      document.createTextNode(
        `${formatTimestamp(event.created_at)}${event.details ? " — " + event.details : ""}`
      )
    );
    list.appendChild(li);
  });
}

/* ------------------------------ main loop -------------------------------- */

async function tick() {
  try {
    await refreshStatus();
    await Promise.all([
      refreshLatest(),
      refreshHistory(),
      refreshAlerts(),
      refreshEvents(),
    ]);
  } catch (error) {
    console.warn("dashboard refresh failed:", error);
    showApiFailure();
  }
}

tick();
setInterval(tick, POLL_INTERVAL_MS);
