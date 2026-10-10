"use strict";

const HEADER_COLORS = {
  digital_high: "#00c8ff", digital_low: "#2f7dff", comms: "#ffc800",
  double_row_even: "#d050ff", double_row_odd: "#ff50c8", power: "#ff4b3e",
  analog_low: "#22e06b", analog_high: "#8ad400", icsp: "#ffffff",
};
// Labels for these columns point away from their sibling column instead of the board centre.
const SIBLINGS = { double_row_even: "double_row_odd", double_row_odd: "double_row_even" };
const PHONE_HINT = /iphone|ipad|continuity|camo|android|droidcam|iriun|epoccam|phone/i;

const $ = (id) => document.getElementById(id);
const video = $("video"), canvas = $("canvas"), ctx = canvas.getContext("2d");
const viewport = $("viewport"), stage = $("stage");

const state = {
  stream: null,
  mode: "live",          // "live" | "frozen"
  image: null,           // ImageBitmap/HTMLImageElement of the frozen frame
  result: null,          // /api/detect response
  selected: new Set(),   // indices of highlighted pins
  hover: -1,
  view: { s: 1, x: 0, y: 0 },
};

// ---------------------------------------------------------------- status

function log(msg, isError = false) {
  $("log").textContent = msg;
  $("log").parentElement.classList.toggle("error", isError);
}

function setMode(mode) {
  state.mode = mode;
  $("mode").textContent = mode === "live" ? "LIVE" : "FROZEN";
  video.hidden = mode !== "live";
  canvas.hidden = mode !== "frozen";
  $("empty").hidden = mode === "frozen" || !!state.stream;
}

// ---------------------------------------------------------------- camera

async function listCameras() {
  if (!navigator.mediaDevices?.getUserMedia) {
    log("Camera API unavailable - open the site via http://localhost:8000", true);
    return;
  }
  try {
    // Device labels are only exposed after permission has been granted once.
    const tmp = await navigator.mediaDevices.getUserMedia({ video: true });
    tmp.getTracks().forEach((t) => t.stop());
  } catch (e) {
    log(`Camera permission denied or no camera: ${e.message}`, true);
  }
  const devices = (await navigator.mediaDevices.enumerateDevices()).filter((d) => d.kind === "videoinput");
  const sel = $("camera-select");
  const prev = sel.value;
  sel.innerHTML = "";
  devices.forEach((d, i) => {
    const o = document.createElement("option");
    o.value = d.deviceId;
    o.textContent = d.label || `Camera ${i + 1}`;
    sel.appendChild(o);
  });
  if (!devices.length) {
    stopCamera();
    log("No cameras found. Connect the phone as a webcam and press REFRESH.", true);
    return;
  }
  const phone = devices.find((d) => PHONE_HINT.test(d.label));
  sel.value = devices.some((d) => d.deviceId === prev) ? prev : (phone || devices[0]).deviceId;
  await startCamera(sel.value);
}

async function startCamera(deviceId) {
  stopCamera();
  try {
    state.stream = await navigator.mediaDevices.getUserMedia({
      video: { deviceId: { exact: deviceId }, width: { ideal: 3840 }, height: { ideal: 2160 } },
      audio: false,
    });
  } catch (e) {
    log(`Could not open camera: ${e.message}`, true);
    return;
  }
  video.srcObject = state.stream;
  await video.play().catch(() => {});
  const label = $("camera-select").selectedOptions[0]?.textContent || "camera";
  $("cam-dot").classList.add("on");
  $("cam-state").textContent = label.toUpperCase().slice(0, 28);
  video.onloadedmetadata = () => { $("res").textContent = `${video.videoWidth}×${video.videoHeight}`; };
  if (state.mode === "live") setMode("live");
  log(`Streaming from ${label}`);
}

function stopCamera() {
  state.stream?.getTracks().forEach((t) => t.stop());
  state.stream = null;
  $("cam-dot").classList.remove("on");
  $("cam-state").textContent = "OFFLINE";
}

// ---------------------------------------------------------------- capture + detect

async function capture() {
  if (!state.stream || !video.videoWidth) { log("No live camera to capture from.", true); return; }
  const off = document.createElement("canvas");
  off.width = video.videoWidth;
  off.height = video.videoHeight;
  off.getContext("2d").drawImage(video, 0, 0);
  const bitmap = await createImageBitmap(off);
  const blob = await new Promise((r) => off.toBlob(r, "image/jpeg", 0.95));
  await detect(blob, bitmap, "snapshot.jpg");
}

async function upload(file) {
  // Browsers can't decode HEIC, so let the server return the image it analysed.
  await detect(file, null, file.name);
}

async function detect(blob, bitmap, name) {
  const btn = $("capture-btn");
  btn.disabled = true;
  log("Running detection…");
  const form = new FormData();
  form.append("image", blob, name);
  try {
    const res = await fetch(`/api/detect?return_image=${bitmap ? 0 : 1}`, { method: "POST", body: form });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || res.statusText);
    if (!bitmap) {
      const img = new Image();
      img.src = `data:image/jpeg;base64,${data.image_jpeg}`;
      await img.decode();
      bitmap = img;
    }
    state.image = bitmap;
    state.result = data;
    state.selected.clear();
    canvas.width = data.width;
    canvas.height = data.height;
    setMode("frozen");
    resetView();
    renderTable();
    draw();
    const det = data.pins.filter((p) => p.detected).length;
    $("detect-ms").textContent = `${data.detect_ms} MS`;
    $("pin-count").textContent = `${det}/${data.pins.length}`;
    $("res").textContent = `${data.width}×${data.height}`;
    log(`Detected ${data.pins.length} pins (${det} matched to pads, ${data.pins.length - det} inferred) in ${data.detect_ms} ms`);
  } catch (e) {
    log(`Detection failed: ${e.message}`, true);
  } finally {
    btn.disabled = false;
  }
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
}

function toggleSelect(i, additive) {
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
  if (state.mode !== "frozen" || !state.result) return -1;
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

$("capture-btn").onclick = capture;
$("live-btn").onclick = () => { setMode("live"); resetView(); };
$("refresh-btn").onclick = listCameras;
$("camera-select").onchange = (e) => startCamera(e.target.value);
$("upload").onchange = (e) => { if (e.target.files[0]) upload(e.target.files[0]); e.target.value = ""; };
["show-boxes", "show-labels", "show-outline"].forEach((id) => ($(id).onchange = draw));
$("filter").oninput = applyFilter;
document.querySelector("#pin-table tbody").onclick = (e) => {
  const tr = e.target.closest("tr");
  if (tr) toggleSelect(Number(tr.dataset.index), e.shiftKey || e.metaKey || e.ctrlKey);
};

window.addEventListener("keydown", (e) => {
  if (e.target.tagName === "INPUT" || e.target.tagName === "SELECT") return;
  if (e.code === "Space") { e.preventDefault(); capture(); }
  else if (e.key === "l" || e.key === "L") { setMode("live"); resetView(); }
  else if (e.key === "0") resetView();
  else if (e.key === "Escape") { state.selected.clear(); renderTable(); draw(); }
});

navigator.mediaDevices?.addEventListener?.("devicechange", listCameras);

setMode("live");
listCameras();
