"use strict";

const HEADER_COLORS = {
  digital_high: "#00c8ff", digital_low: "#2f7dff", comms: "#ffc800",
  double_row_even: "#d050ff", double_row_odd: "#ff50c8", power: "#ff4b3e",
  analog_low: "#22e06b", analog_high: "#8ad400", icsp: "#ffffff",
};
// Labels for these columns point away from their sibling column instead of the board centre.
const SIBLINGS = { double_row_even: "double_row_odd", double_row_odd: "double_row_even" };
const $ = (id) => document.getElementById(id);
const canvas = $("canvas"), ctx = canvas.getContext("2d");
const viewport = $("viewport"), stage = $("stage");
const probeJob = {
  id: null, running: false, manualMode: false, stage: null,
  hasRun: false, requestedPins: [], lastResult: null, pollTimer: null,
};

const state = {
  image: null,           // ImageBitmap/HTMLImageElement of the analysed photo
  result: null,          // detection response
  selected: new Set(),   // indices of highlighted pins
  hover: -1,
  view: { s: 1, x: 0, y: 0 },
};

// ---------------------------------------------------------------- status

function log(msg, isError = false) {
  $("log").textContent = msg;
  $("log").parentElement.title = msg;
  $("log").parentElement.classList.toggle("error", isError);
}

function showCanvas(on) {
  canvas.hidden = !on;
  $("empty").hidden = on;
}

// ---------------------------------------------------------------- upload

async function upload(file) {
  log("Running detection…");
  const form = new FormData();
  form.append("image", file, file.name);
  try {
    // Browsers can't decode HEIC, so the server returns the image it analysed.
    const res = await fetch("/api/detect?return_image=1", { method: "POST", body: form });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || res.statusText);
    showResult(data, await loadImage(`data:image/jpeg;base64,${data.image_jpeg}`), "UPLOAD · ");
  } catch (e) {
    log(`Detection failed: ${e.message}`, true);
  }
}

async function loadImage(src) {
  const img = new Image();
  img.src = src;
  await img.decode();
  return img;
}

function showResult(data, image, source) {
  state.image = image;
  state.result = data;
  state.selected.clear();
  canvas.width = data.width;
  canvas.height = data.height;
  showCanvas(true);
  resetView();
  renderTable();
  draw();
  const det = data.pins.filter((p) => p.detected).length;
  $("detect-ms").textContent = `${data.detect_ms} MS`;
  $("pin-count").textContent = `${det}/${data.pins.length}`;
  $("res").textContent = `${data.width}×${data.height}`;
  log(`${source}Detected ${data.pins.length} pins (${det} matched to pads, ${data.pins.length - det} inferred) in ${data.detect_ms} ms`);
}

// ---------------------------------------------------------------- phone link

const phone = { ws: null, connected: false, retryMs: 500, urls: [], qrExpanded: true };

async function loadSession() {
  try {
    const res = await fetch("/api/session", { cache: "no-store" });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || res.statusText);
    phone.urls = data.urls;
    const sel = $("phone-net");
    sel.innerHTML = "";
    data.urls.forEach((u) => {
      const o = document.createElement("option");
      o.value = u.ip;
      o.textContent = `${u.ip}${u.hotspot ? "  (iPhone hotspot)" : ""}`;
      sel.appendChild(o);
    });
    sel.hidden = data.urls.length < 2;
    showQr();
  } catch (e) {
    $("phone-url").textContent = e.message;
  }
}

function showQr() {
  const u = phone.urls.find((x) => x.ip === $("phone-net").value) || phone.urls[0];
  if (!u) {
    $("phone-qr").hidden = true;
    $("phone-show-qr").hidden = true;
    $("phone-url").textContent = "No network. Turn on the phone's Personal Hotspot, connect it by USB, then press REFRESH.";
    return;
  }
  $("phone-qr").src = `/api/qr.svg?ip=${encodeURIComponent(u.ip)}&t=${Date.now()}`;
  $("phone-url").textContent = u.url;
  syncQrVisibility();
}

function syncQrVisibility() {
  const hasUrl = phone.urls.length > 0;
  const visible = hasUrl && phone.qrExpanded && !phone.connected;
  $("phone-qr").hidden = !visible;
  $("phone-show-qr").hidden = !hasUrl;
  $("phone-show-qr").textContent = phone.qrExpanded ? "HIDE QR" : "SHOW QR";
  $("phone-show-qr").setAttribute("aria-expanded", String(visible));
}

function setPhoneStatus(connected, devices = []) {
  const changed = connected !== phone.connected;
  phone.connected = connected;
  $("phone-dot").classList.toggle("on", connected);
  $("phone-state").textContent = connected ? "CONNECTED" : "WAITING";
  $("phone-status").textContent = connected
    ? `Phone connected${devices.length > 1 ? ` (${devices.length})` : ""}. Take a photo on the phone, or press REQUEST PHOTO.`
    : "Scan with the phone's Camera app to connect.";
  $("phone-request").disabled = !connected;
  syncQrVisibility();
  if (changed) log(connected ? "Phone connected." : "Phone disconnected - it will reconnect automatically when the page is reopened.");
}

function connectPhoneLink() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/ws?role=laptop`);
  phone.ws = ws;
  ws.onopen = () => { phone.retryMs = 500; };
  ws.onmessage = async (ev) => {
    const msg = JSON.parse(ev.data);
    if (msg.type === "phone_status") setPhoneStatus(msg.connected, msg.devices);
    else if (msg.type === "phone_photo_received") log(`Photo received from phone (${(msg.bytes / 1e6).toFixed(1)} MB) - detecting…`);
    else if (msg.type === "detection_failed") log(`Phone photo: ${msg.detail}`, true);
    else if (msg.type === "detection_result") {
      const img = await loadImage(`/api/latest.jpg?id=${msg.image_id}`);
      showResult(msg.result, img, "PHONE · ");
    }
  };
  ws.onclose = () => {
    phone.ws = null;
    setPhoneStatus(false);
    $("phone-state").textContent = "SERVER?";
    setTimeout(() => { loadSession(); connectPhoneLink(); }, phone.retryMs = Math.min(phone.retryMs * 2, 5000));
  };
}

function requestPhonePhoto() {
  if (phone.ws?.readyState !== 1 || !phone.connected) { log("No phone connected.", true); return; }
  phone.ws.send(JSON.stringify({ type: "capture_request" }));
  log("Asked the phone for a photo - tap TAKE PHOTO on the phone.");
}
// ---------------------------------------------------------------- drawing

function centroid(points) {
  const c = points.reduce((a, p) => [a[0] + p[0], a[1] + p[1]], [0, 0]);
  return [c[0] / points.length, c[1] / points.length];
}

// Direction (unit vector) each pin's label should point: perpendicular to its
// header row, away from the board centre (or from the sibling column).
function labelDirections(pins, corners) {
  const groups = {};
  pins.forEach((p, i) => (groups[p.header] ||= []).push(i));
  const centres = {};
  for (const [h, idx] of Object.entries(groups)) centres[h] = centroid(idx.map((i) => [pins[i].x, pins[i].y]));
  const boardC = centroid(corners);
  const dirs = new Array(pins.length);

  for (const [h, idx] of Object.entries(groups)) {
    // Principal axis of the group: a header row, or the long side of a pin grid (e.g. ICSP 2x3).
    const [cx, cy] = centres[h];
    let sxx = 0, syy = 0, sxy = 0;
    for (const i of idx) {
      const dx = pins[i].x - cx, dy = pins[i].y - cy;
      sxx += dx * dx; syy += dy * dy; sxy += dx * dy;
    }
    const theta = 0.5 * Math.atan2(2 * sxy, sxx - syy);
    const major = (sxx + syy) / 2 + Math.hypot((sxx - syy) / 2, sxy);
    const minor = (sxx + syy) / 2 - Math.hypot((sxx - syy) / 2, sxy);
    const isRow = minor < 0.05 * major;
    // Rows point away from the board centre (or sibling column); grids split around their own centre.
    const ref = isRow ? (SIBLINGS[h] ? centres[SIBLINGS[h]] : boardC) : centres[h];
    const n0 = [-Math.sin(theta), Math.cos(theta)];
    for (const i of idx) {
      const p = pins[i];
      const sgn = n0[0] * (p.x - ref[0]) + n0[1] * (p.y - ref[1]) < 0 ? -1 : 1;
      dirs[i] = [n0[0] * sgn, n0[1] * sgn];
    }
  }
  return dirs;
}

function boxSize(box) {
  return Math.hypot(box[1][0] - box[0][0], box[1][1] - box[0][1]);
}

function draw() {
  const r = state.result;
  if (!r || !state.image) return;
  ctx.drawImage(state.image, 0, 0, canvas.width, canvas.height);

  const showBoxes = $("show-boxes").checked, showLabels = $("show-labels").checked;
  const hasSel = state.selected.size > 0;
  const unit = Math.max(1, canvas.width / 1500);

  if ($("show-outline").checked) {
    ctx.save();
    ctx.setLineDash([12 * unit, 8 * unit]);
    ctx.strokeStyle = "#f2b705";
    ctx.lineWidth = 2 * unit;
    ctx.beginPath();
    r.board_corners.forEach(([x, y], i) => (i ? ctx.lineTo(x, y) : ctx.moveTo(x, y)));
    ctx.closePath();
    ctx.stroke();
    ctx.restore();
  }

  if (hasSel) {
    ctx.fillStyle = "rgba(10, 12, 14, 0.55)";
    ctx.fillRect(0, 0, canvas.width, canvas.height);
  }

  const dirs = labelDirections(r.pins, r.board_corners);
  // Image pixels per screen pixel, so highlighted labels stay readable at any zoom.
  const rect = canvas.getBoundingClientRect();
  const imgPerScreen = rect.width ? canvas.width / rect.width : 1;
  r.pins.forEach((p, i) => {
    const active = !hasSel || state.selected.has(i);
    const hot = i === state.hover || (hasSel && state.selected.has(i));
    const color = HEADER_COLORS[p.header] || "#fff";
    const size = boxSize(p.box);

    if (showBoxes || hot) {
      ctx.beginPath();
      p.box.forEach(([x, y], k) => (k ? ctx.lineTo(x, y) : ctx.moveTo(x, y)));
      ctx.closePath();
      ctx.lineWidth = (hot ? 3 : 1.5) * unit;
      ctx.strokeStyle = hot ? "#f2b705" : color;
      ctx.globalAlpha = active ? 1 : 0.25;
      ctx.setLineDash(p.detected ? [] : [4 * unit, 3 * unit]);
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.globalAlpha = 1;
    }

    if ((showLabels && active) || hot) {
      const [nx, ny] = dirs[i];
      const fontPx = hot ? Math.max(size * 0.75, 15 * imgPerScreen) : Math.max(9, size * 0.5);
      let ang = Math.atan2(ny, nx);
      let align = "left";
      if (Math.cos(ang) < -1e-3) { ang += Math.PI; align = "right"; }
      const off = Math.max(size * 0.65, hot ? 8 * imgPerScreen : 0);
      ctx.save();
      ctx.translate(p.x + nx * off, p.y + ny * off);
      ctx.rotate(ang);
      ctx.font = `600 ${fontPx}px ui-monospace, Menlo, Consolas, monospace`;
      ctx.textAlign = align;
      ctx.textBaseline = "middle";
      ctx.lineWidth = Math.max(2, fontPx / 4);
      ctx.strokeStyle = "rgba(0,0,0,0.85)";
      ctx.strokeText(p.label, 0, 0);
      ctx.fillStyle = hot ? "#f2b705" : color;
      ctx.fillText(p.label, 0, 0);
      ctx.restore();
    }
  });
}

// ---------------------------------------------------------------- pin table

function matches(p, q) {
  if (!q) return true;
  if (p.label.toUpperCase().startsWith(q)) return true;
  return q.length >= 3 && p.header.toUpperCase().split("_").some((w) => w.startsWith(q));
}

function renderTable() {
  const tbody = document.querySelector("#pin-table tbody");
  tbody.innerHTML = "";
  if (!state.result) return;
  const q = $("filter").value.trim().toUpperCase();
  state.result.pins.forEach((p, i) => {
    if (!matches(p, q)) return;
    const tr = document.createElement("tr");
    tr.dataset.index = i;
    if (state.selected.has(i)) tr.classList.add("selected");
    tr.innerHTML =
      `<td><span class="swatch" style="background:${HEADER_COLORS[p.header] || "#fff"}"></span>${p.label}</td>` +
      `<td>${p.header.replace(/_/g, " ").toUpperCase()}</td>` +
      `<td class="${p.detected ? "st-ok" : "st-inf"}" title="${p.detected ? "Matched to a detected pad" : "Position inferred from board layout"}">` +
      `${p.detected ? "OK" : "EST"}</td>`;
    tbody.appendChild(tr);
  });
  $("sel-info").textContent = state.selected.size ? `· ${state.selected.size} SELECTED` : "";
  updateProbeControls();
}

function updateProbeControls() {
  const button = $("probe-start");
  const manualReady = $("manual-ready");
  const awaitingManualPosition =
    probeJob.running && probeJob.manualMode && probeJob.stage === "manual_positioning";
  button.disabled = probeJob.running || !state.result || state.selected.size === 0;
  button.textContent = probeJob.running ? "JOB RUNNING" : probeJob.hasRun ? "RETRY PROBE" : "PROBE SELECTED";
  $("manual-mode").disabled = probeJob.running;
  manualReady.hidden = !awaitingManualPosition;
  manualReady.disabled = !awaitingManualPosition;
}

function showJobStatus(job) {
  probeJob.stage = job.stage;
  const current = job.current_pin;
  const progress = `${job.completed_pins}/${job.total_pins}`;
  if (job.status === "failed") {
    $("manual-ready").hidden = true;
    const pin = current ? ` at ${current.label}` : " during preflight";
    const stage = job.failed_stage ? ` (${job.failed_stage})` : "";
    $("job-status").textContent = `FAILED${pin}${stage}: ${job.error}`;
    log(`Probe job failed${pin}${stage}: ${job.error}`, true);
    showResults(job);
    return;
  }
  if (job.status === "complete") {
    $("manual-ready").hidden = true;
    const results = (job.results || []).map((result) => {
      const protocol = result.capture?.protocol_detection?.classification || "unknown";
      return `${result.label}: ${protocol}`;
    });
    const summary = results.length ? ` · ${results.join(" · ")}` : "";
    $("job-status").textContent = `COMPLETE · ${progress} PINS${summary}`;
    log(`Probe job complete · ${progress} pins`);
    showResults(job);
    return;
  }
  if (job.manual_mode && job.stage === "manual_positioning" && current) {
    $("job-status").textContent = `Position the probe on ${current.label}, then start the read and hold steady until it completes. · ${progress} PINS`;
    updateProbeControls();
    log(`Manual probe · position on ${current.label} · ${progress} pins`);
    return;
  }
  if (job.manual_mode && job.stage === "capturing signal" && current) {
    $("job-status").textContent = `Reading ${current.label} for about 2.5 seconds. Keep the probe steady. · ${progress} PINS`;
    updateProbeControls();
    log(`Manual probe · reading ${current.label}`);
    return;
  }
  if (job.manual_mode && job.stage === "processing signal" && current) {
    $("job-status").textContent = `Reading complete for ${current.label}. Processing capture; this may take 30–45 seconds. · ${progress} PINS`;
    updateProbeControls();
    log(`Manual probe · reading complete for ${current.label}; processing capture`);
    return;
  }
  const activePin = current ? `${current.label} · ` : "";
  $("job-status").textContent = `${activePin}${job.stage.toUpperCase()} · ${progress} PINS`;
  log(`Probe job · ${activePin}${job.stage} · ${progress} pins`);
  updateProbeControls();
}

function pinReportData(pin, job) {
  const capture = (job.results || []).find((result) => result.pin_id === pin.pin_id)?.capture;
  if (capture?.protocol_detection) {
    const detection = capture.protocol_detection;
    const classification = detection.classification || "unknown";
    const candidates = detection.candidates || [];
    const candidate = candidates.find((item) => item.protocol === classification) || candidates[0];
    const unknown = ["unknown", "ambiguous"].includes(classification.toLowerCase());
    const additional = candidate || { score_note: detection.score_note, candidates };
    const decodedText = /^UART\b/i.test(classification)
      ? decodeUartPreview(candidate?.evidence?.decoded_bytes_preview)
      : null;
    if (job.status === "failed" && job.current_pin?.pin_id === pin.pin_id) {
      additional.job_failure = { failed_stage: job.failed_stage, error: job.error };
    }
    return {
      status: unknown ? "Unknown" : "Success",
      protocol: unknown ? classification[0].toUpperCase() + classification.slice(1) : classification,
      additional,
      decodedText,
    };
  }

  const isFailedPin = job.current_pin?.pin_id === pin.pin_id;
  const failedBeforePin = job.status === "failed" && !job.current_pin;
  return {
    status: "Fail",
    protocol: "Not detected",
    additional: isFailedPin || failedBeforePin
      ? { failed_stage: job.failed_stage, error: job.error }
      : { error: "Not captured because the job stopped before reaching this pin." },
  };
}

function decodeUartPreview(bytes) {
  if (!Array.isArray(bytes)) return "No decoded bytes in the report.";
  const decoded = new TextDecoder().decode(Uint8Array.from(bytes));
  return decoded
    .replace(/\r\n?/g, "\n")
    .replace(/[\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F]/g,
      (character) => `\\x${character.charCodeAt(0).toString(16).padStart(2, "0")}`);
}

function showResults(job) {
  probeJob.lastResult = job;
  const list = $("results-list");
  list.replaceChildren();
  const pins = probeJob.requestedPins.length
    ? probeJob.requestedPins
    : (job.results || []).map(({ pin_id, label }) => ({ pin_id, label }));

  for (const pin of pins) {
    const report = pinReportData(pin, job);
    const item = document.createElement("article");
    item.className = `result-item result-${report.status.toLowerCase()}`;

    const heading = document.createElement("div");
    heading.className = "result-heading";
    const title = document.createElement("h3");
    title.textContent = `Pin ${pin.label} (${pin.pin_id})`;
    const status = document.createElement("strong");
    status.className = "result-status";
    status.textContent = report.status;
    heading.append(title, status);

    const protocol = document.createElement("p");
    protocol.className = "result-protocol";
    protocol.textContent = `Detected Protocol: ${report.protocol}`;
    item.append(heading, protocol);
    if (report.decodedText !== null) {
      const decodedLabel = document.createElement("div");
      decodedLabel.className = "result-data-label";
      decodedLabel.textContent = "Decoded UART Text Preview";
      const decoded = document.createElement("pre");
      decoded.className = "result-decoded-text";
      decoded.textContent = report.decodedText;
      item.append(decodedLabel, decoded);
    }
    const dataLabel = document.createElement("div");
    dataLabel.className = "result-data-label";
    dataLabel.textContent = "Additional Data";
    const data = document.createElement("pre");
    data.textContent = JSON.stringify(report.additional, null, 2);
    item.append(dataLabel, data);
    list.append(item);
  }

  const progress = `${job.completed_pins}/${job.total_pins} pins`;
  const failure = job.status === "failed" ? ` · ${job.failed_stage}: ${job.error}` : "";
  $("results-summary").textContent = `${job.status.toUpperCase()} · ${progress}${failure}`;
  if (!$("results-dialog").open) $("results-dialog").showModal();
}

function downloadMarkdownReport() {
  if (!probeJob.lastResult) return;
  const job = probeJob.lastResult;
  const pins = probeJob.requestedPins.length
    ? probeJob.requestedPins
    : (job.results || []).map(({ pin_id, label }) => ({ pin_id, label }));
  const sections = pins.map((pin) => {
    const report = pinReportData(pin, job);
    return [
      `## Pin ${pin.label} (${pin.pin_id})`,
      `Result: ${report.status}`,
      `Detected Protocol: ${report.protocol}`,
      ...(report.decodedText === null ? [] : [`Decoded Text Preview: ${JSON.stringify(report.decodedText)}`]),
      "Additional Data:",
      "```json",
      JSON.stringify(report.additional, null, 2),
      "```",
    ].join("\n");
  });
  const blob = new Blob([sections.join("\n\n")], { type: "text/markdown;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `probe-report-${job.job_id.slice(0, 8)}.md`;
  link.click();
  URL.revokeObjectURL(url);
}

async function pollProbeJob() {
  if (!probeJob.id) return;
  try {
    const res = await fetch(`/api/jobs/${encodeURIComponent(probeJob.id)}`, { cache: "no-store" });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || res.statusText);
    showJobStatus(data);
    if (data.status === "queued" || data.status === "running") {
      probeJob.pollTimer = setTimeout(pollProbeJob, 1000);
      return;
    }
    probeJob.running = false;
    probeJob.id = null;
    probeJob.stage = null;
    probeJob.hasRun = true;
    updateProbeControls();
  } catch (e) {
    $("job-status").textContent = `STATUS CHECK FAILED: ${e.message}`;
    log(`Could not read probe job status: ${e.message}`, true);
    probeJob.pollTimer = setTimeout(pollProbeJob, 2000);
  }
}

async function startProbeJob() {
  if (probeJob.running || !state.result || !state.selected.size) return;
  const selectedPins = [...state.selected].map((index) => state.result.pins[index]);
  if (selectedPins.some((pin) => !pin.pin_id)) {
    log("This pin result is missing backend pin IDs. Upload or detect the photo again.", true);
    return;
  }

  probeJob.running = true;
  probeJob.requestedPins = selectedPins.map(({ pin_id, label }) => ({ pin_id, label }));
  $("job-status").textContent = "STARTING JOB…";
  updateProbeControls();
  try {
    const res = await fetch("/api/jobs/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        board: state.result.board,
        pin_ids: selectedPins.map((pin) => pin.pin_id),
        manual_mode: $("manual-mode").checked,
      }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || res.statusText);
    probeJob.id = data.job_id;
    probeJob.manualMode = data.manual_mode;
    probeJob.lastResult = null;
    showJobStatus(data);
    pollProbeJob();
  } catch (e) {
    probeJob.running = false;
    probeJob.manualMode = false;
    probeJob.stage = null;
    $("manual-ready").hidden = true;
    $("job-status").textContent = `COULD NOT START: ${e.message}`;
    updateProbeControls();
    log(`Could not start probe job: ${e.message}`, true);
  }
}

async function confirmManualPosition() {
  if (!probeJob.id || !probeJob.manualMode || probeJob.stage !== "manual_positioning") return;
  $("manual-ready").disabled = true;
  $("job-status").textContent = "Starting scope capture…";
  try {
    const res = await fetch(`/api/jobs/${encodeURIComponent(probeJob.id)}/ready`, {
      method: "POST",
      cache: "no-store",
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || res.statusText);
    showJobStatus(data);
    pollProbeJob();
  } catch (e) {
    $("manual-ready").disabled = false;
    $("job-status").textContent = `Could not start capture: ${e.message}`;
    log(`Could not confirm probe position: ${e.message}`, true);
  }
}

function toggleSelect(i, additive) {
  probeJob.hasRun = false;
  if (!additive) {
    const only = state.selected.size === 1 && state.selected.has(i);
    state.selected.clear();
    if (!only) state.selected.add(i);
  } else if (state.selected.has(i)) {
    state.selected.delete(i);
  } else {
    state.selected.add(i);
  }
  renderTable();
  draw();
}

function applyFilter() {
  probeJob.hasRun = false;
  const q = $("filter").value.trim().toUpperCase();
  state.selected.clear();
  if (q && state.result) {
    state.result.pins.forEach((p, i) => { if (matches(p, q)) state.selected.add(i); });
  }
  renderTable();
  draw();
}

// ---------------------------------------------------------------- zoom / pan / hover

let redrawPending = false;
function applyView() {
  const v = state.view;
  stage.style.transform = `translate(${v.x}px, ${v.y}px) scale(${v.s})`;
  $("zoom-level").textContent = `${Math.round(v.s * 100)}%`;
  if (state.selected.size && !redrawPending) {
    redrawPending = true;
    requestAnimationFrame(() => { redrawPending = false; draw(); });
  }
}

function resetView() {
  state.view = { s: 1, x: 0, y: 0 };
  applyView();
}

function imageCoords(ev) {
  const rect = canvas.getBoundingClientRect();
  return [
    ((ev.clientX - rect.left) * canvas.width) / rect.width,
    ((ev.clientY - rect.top) * canvas.height) / rect.height,
  ];
}

function pointInPoly([x, y], poly) {
  let inside = false;
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
    const [xi, yi] = poly[i], [xj, yj] = poly[j];
    if ((yi > y) !== (yj > y) && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) inside = !inside;
  }
  return inside;
}

function pinAt(ev) {
  if (!state.result) return -1;
  const pt = imageCoords(ev);
  return state.result.pins.findIndex((p) => pointInPoly(pt, p.box));
}

viewport.addEventListener("wheel", (ev) => {
  ev.preventDefault();
  const v = state.view;
  const rect = viewport.getBoundingClientRect();
  const mx = ev.clientX - rect.left, my = ev.clientY - rect.top;
  const s = Math.min(20, Math.max(1, v.s * Math.exp(-ev.deltaY * 0.0015)));
  v.x = mx - ((mx - v.x) * s) / v.s;
  v.y = my - ((my - v.y) * s) / v.s;
  v.s = s;
  if (s === 1) { v.x = 0; v.y = 0; }
  applyView();
}, { passive: false });

let drag = null;
viewport.addEventListener("mousedown", (ev) => {
  drag = { x: ev.clientX, y: ev.clientY, vx: state.view.x, vy: state.view.y, moved: false };
  viewport.classList.add("dragging");
});
window.addEventListener("mouseup", (ev) => {
  if (drag && !drag.moved) {
    const i = pinAt(ev);
    if (i >= 0) toggleSelect(i, ev.shiftKey || ev.metaKey || ev.ctrlKey);
  }
  drag = null;
  viewport.classList.remove("dragging");
});
window.addEventListener("mousemove", (ev) => {
  if (drag) {
    const dx = ev.clientX - drag.x, dy = ev.clientY - drag.y;
    if (Math.abs(dx) + Math.abs(dy) > 3) drag.moved = true;
    state.view.x = drag.vx + dx;
    state.view.y = drag.vy + dy;
    applyView();
    return;
  }
  if (!viewport.contains(ev.target)) return;
  const i = pinAt(ev);
  const tip = $("tooltip");
  if (i !== state.hover) { state.hover = i; draw(); }
  if (i >= 0) {
    const p = state.result.pins[i];
    const rect = viewport.getBoundingClientRect();
    tip.textContent = `${p.label}  ·  ${p.header.replace(/_/g, " ").toUpperCase()}  ·  ${p.detected ? "DETECTED" : "INFERRED"}`;
    tip.style.left = `${ev.clientX - rect.left + 14}px`;
    tip.style.top = `${ev.clientY - rect.top + 14}px`;
    tip.hidden = false;
  } else {
    tip.hidden = true;
  }
});

// ---------------------------------------------------------------- wiring

$("phone-request").onclick = requestPhonePhoto;
$("probe-start").onclick = startProbeJob;
$("manual-ready").onclick = confirmManualPosition;
$("results-close").onclick = () => $("results-dialog").close();
$("results-download").onclick = downloadMarkdownReport;
$("results-dialog").addEventListener("click", (event) => {
  if (event.target === $("results-dialog")) $("results-dialog").close();
});
$("phone-net").onchange = showQr;
$("phone-show-qr").onclick = () => {
  phone.qrExpanded = !phone.qrExpanded;
  syncQrVisibility();
};
$("phone-refresh").onclick = loadSession;
$("upload").onchange = (e) => { if (e.target.files[0]) upload(e.target.files[0]); e.target.value = ""; };
["show-boxes", "show-labels", "show-outline"].forEach((id) => ($(id).onchange = draw));
$("filter").oninput = applyFilter;
document.querySelector("#pin-table tbody").onclick = (e) => {
  const tr = e.target.closest("tr");
  if (tr) toggleSelect(Number(tr.dataset.index), e.shiftKey || e.metaKey || e.ctrlKey);
};

window.addEventListener("keydown", (e) => {
  if (e.target.tagName === "INPUT" || e.target.tagName === "SELECT") return;
  if (e.code === "Space") { e.preventDefault(); requestPhonePhoto(); }
  else if (e.key === "0") resetView();
  else if (e.key === "Escape") { state.selected.clear(); renderTable(); draw(); }
});

showCanvas(false);
loadSession();
connectPhoneLink();
