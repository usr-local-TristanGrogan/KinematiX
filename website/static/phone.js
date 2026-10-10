"use strict";

// Phone side: take photos with the native camera, send them to the laptop,
// show the result. Works over plain http (no camera API needed).

const $ = (id) => document.getElementById(id);

const params = new URLSearchParams(location.search);
const code = params.get("s") || localStorage.getItem("pinfinder.code") || "";
if (params.get("s")) localStorage.setItem("pinfinder.code", params.get("s"));

let ws = null;
let retryMs = 500;
let busy = false;

function setLink(text, cls) {
  $("link-state").textContent = text;
  $("dot").className = `dot ${cls || ""}`;
}

function showError(msg) {
  $("error").textContent = msg;
  $("error").hidden = !msg;
}

// ---------------------------------------------------------------- connection

async function pair() {
  if (!code) {
    setLink("NOT PAIRED");
    showError("Scan the QR code shown on the laptop to pair this phone.");
    $("shoot").classList.add("disabled");
    return;
  }
  try {
    const res = await fetch(`/api/phone/hello?s=${encodeURIComponent(code)}`, { cache: "no-store" });
    if (res.status === 403) {
      setLink("EXPIRED");
      showError("This pairing has expired (the laptop server restarted). Scan the QR code on the laptop again.");
      $("shoot").classList.add("disabled");
      return;
    }
    if (!res.ok) throw new Error(res.statusText);
  } catch {
    setLink("OFFLINE");
    showError("Can't reach the laptop. Check the USB cable / hotspot, and that the server is running.");
    setTimeout(pair, retryMs = Math.min(retryMs * 2, 5000));
    return;
  }
  showError("");
  $("shoot").classList.remove("disabled");
  connect();
}

function connect() {
  if (ws && ws.readyState <= 1) return;
  const proto = location.protocol === "https:" ? "wss" : "ws";
  ws = new WebSocket(`${proto}://${location.host}/ws?role=phone&s=${encodeURIComponent(code)}`);
  ws.onopen = () => {
    retryMs = 500;
    setLink("CONNECTED", "ok");
    if (!busy) showError("");
  };
  ws.onmessage = (ev) => {
    const msg = JSON.parse(ev.data);
    if (msg.type === "capture_request") {
      $("request").hidden = false;
      $("shoot").classList.add("attention");
    }
  };
  ws.onclose = () => {
    setLink("RECONNECTING", "busy");
    ws = null;
    // Re-verify the code: a server restart means a new one.
    setTimeout(pair, retryMs = Math.min(retryMs * 2, 5000));
  };
}

// iOS suspends the page when the screen locks or the Camera app is open.
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible" && (!ws || ws.readyState > 1)) {
    retryMs = 500;
    pair();
  }
});

// Keep proxies/NAT from dropping an idle socket.
setInterval(() => { if (ws && ws.readyState === 1) ws.send(JSON.stringify({ type: "ping" })); }, 20000);

// ---------------------------------------------------------------- photo

function setResult(title, sub, cls) {
  $("result").hidden = false;
  $("result-title").textContent = title;
  $("result-sub").textContent = sub || "";
  document.querySelector(".result-head").className = `result-head ${cls || ""}`;
}

function setProgress(frac) {
  $("progress").hidden = frac == null;
  $("progress-bar").style.width = `${Math.round((frac || 0) * 100)}%`;
}

function upload(file) {
  return new Promise((resolve, reject) => {
    const form = new FormData();
    form.append("image", file, file.name || "photo.jpg");
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `/api/phone/upload?s=${encodeURIComponent(code)}`);
    xhr.upload.onprogress = (e) => { if (e.lengthComputable) setProgress(0.9 * e.loaded / e.total); };
    xhr.upload.onload = () => { setProgress(0.95); setResult("DETECTING…", ""); };
    xhr.onload = () => {
      let data = {};
      try { data = JSON.parse(xhr.responseText); } catch { /* non-JSON error */ }
      if (xhr.status >= 200 && xhr.status < 300) resolve(data);
      else reject(new Error(data.detail || `Error ${xhr.status}`));
    };
    xhr.onerror = () => reject(new Error("Upload failed - connection to the laptop was lost."));
    xhr.send(form);
  });
}

async function drawPreview(bitmap, data) {
  const cv = $("preview");
  if (!bitmap) { cv.width = 0; cv.height = 0; return; }
  const scale = Math.min(1, 1400 / Math.max(bitmap.width, bitmap.height));
  cv.width = Math.round(bitmap.width * scale);
  cv.height = Math.round(bitmap.height * scale);
  const g = cv.getContext("2d");
  g.drawImage(bitmap, 0, 0, cv.width, cv.height);
  if (!data) return;
  // Server coordinates are in the analysed (EXIF-rotated) image.
  const sx = cv.width / data.width, sy = cv.height / data.height;
  const r = Math.max(3, cv.width / 220);
  for (const p of data.pins) {
    g.beginPath();
    g.arc(p.x * sx, p.y * sy, r, 0, Math.PI * 2);
    g.fillStyle = p.detected ? "rgba(63,185,80,0.9)" : "rgba(242,183,5,0.9)";
    g.fill();
  }
}

async function onPhoto(file) {
  if (!file) return;
  busy = true;
  showError("");
  $("request").hidden = true;
  $("shoot").classList.remove("attention");
  $("shoot").classList.add("compact", "disabled");
  setLink("SENDING", "busy");
  setResult("SENDING…", `${(file.size / 1e6).toFixed(1)} MB`);
  setProgress(0);

  let bitmap = null;
  try { bitmap = await createImageBitmap(file); } catch { /* e.g. HEIC on non-Safari */ }
  await drawPreview(bitmap, null);

  try {
    const data = await upload(file);
    setProgress(null);
    setResult(`${data.detected}/${data.total} PINS`, `${data.detect_ms} ms · shown on laptop`, "ok");
    await drawPreview(bitmap, data);
  } catch (e) {
    setProgress(null);
    setResult("NOT FOUND", "", "bad");
    showError(`${e.message}`);
  } finally {
    busy = false;
    $("shoot").classList.remove("disabled");
    $("shoot").querySelector(".shoot-label").textContent = "RETAKE";
    setLink(ws && ws.readyState === 1 ? "CONNECTED" : "RECONNECTING", ws && ws.readyState === 1 ? "ok" : "busy");
  }
}

$("file").addEventListener("change", (e) => {
  const file = e.target.files[0];
  e.target.value = "";  // allow choosing again
  onPhoto(file);
});

pair();
