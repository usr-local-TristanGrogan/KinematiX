"""Local web UI for pin detection, with phone pairing.

  python run.py                     # from the repo root (sets everything up)
  python website/server.py          # if dependencies are already installed

The laptop opens http://localhost:8000. A phone on the same network (ideally the
phone's own Personal Hotspot over USB) scans the QR code shown there and uses
its native camera to send photos.
"""

from __future__ import annotations

import argparse
import base64
import io
import ipaddress
import secrets
import sys
import time
from dataclasses import asdict
from pathlib import Path

import cv2
import psutil
import segno
import uvicorn
from fastapi import FastAPI, File, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent))
sys.path.insert(0, str(ROOT.parent / "computer-vision"))
import pin_finder as pf  # noqa: E402
from backend.job_runner import JobBusyError, ManualJobStateError, probe_jobs  # noqa: E402
from backend.pin_coordinates import PinCoordinateMapError  # noqa: E402
from backend.robot_serial import SerialConfigurationError  # noqa: E402

app = FastAPI(title="Pin Finder")

# Below this the layout fit can lock onto the wrong pads (seen with blurry frames).
MIN_DETECTED_FRACTION = 0.3
HINT = ("Make sure the image is in focus (tap the board to focus) and the whole board is visible.")

PORT = 8000
# One pairing code per server run; phones must present it. Rescan after a restart.
SESSION_CODE = secrets.token_urlsafe(6)


class StartJobRequest(BaseModel):
    board: str
    pin_ids: list[str]
    manual_mode: bool = False


# --------------------------------------------------------------------------
# Detection
# --------------------------------------------------------------------------

def run_detection(data: bytes, board: str):
    """Decode + detect. Returns (result dict, analysed image as JPEG bytes)."""
    if board not in pf.BOARDS:
        raise HTTPException(400, f"Unknown board '{board}'")
    try:
        img = pf.load_image(io.BytesIO(data))
    except Exception:
        raise HTTPException(400, "Could not read image (JPEG, PNG or HEIC expected).")

    t0 = time.perf_counter()
    try:
        pins, dbg = pf.find_pins(img, board)
    except RuntimeError as e:
        raise HTTPException(422, f"{e} {HINT}")
    ms = (time.perf_counter() - t0) * 1000

    n_det = sum(p.detected for p in pins)
    if n_det < MIN_DETECTED_FRACTION * len(pins):
        raise HTTPException(422, f"Only {n_det}/{len(pins)} pads found, too few to trust the result. {HINT}")

    # Browsers can't display HEIC and may rotate differently, so keep exactly what was analysed.
    _, jpg = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 90])
    result = {
        "board": board,
        "width": img.shape[1],
        "height": img.shape[0],
        "detect_ms": round(ms, 1),
        "detected": n_det,
        "board_corners": dbg["corners"].tolist(),
        "pins": [
            {"pin_id": f"pin_{index:03d}", **asdict(pin)}
            for index, pin in enumerate(pins)
        ],
    }
    return result, jpg.tobytes()


@app.get("/api/boards")
def boards():
    return {"boards": list(pf.BOARDS)}


@app.post("/api/detect")
async def detect(image: UploadFile = File(...), board: str = "metro_grand_central",
                 return_image: bool = False):
    result, jpg = await run_in_threadpool(run_detection, await image.read(), board)
    if return_image:
        result["image_jpeg"] = base64.b64encode(jpg).decode()
    return result


@app.post("/api/jobs/start")
def start_probe_job(request: Request, payload: StartJobRequest):
    require_local(request)
    if payload.board not in pf.BOARDS:
        raise HTTPException(400, f"Unknown board '{payload.board}'")
    template = pf.BOARDS[payload.board]["template"]()
    pin_metadata = {}
    for pin_id in payload.pin_ids:
        try:
            index = int(pin_id.removeprefix("pin_"))
        except (AttributeError, ValueError) as exc:
            raise HTTPException(400, f"Invalid pin ID {pin_id!r}.") from exc
        if pin_id != f"pin_{index:03d}" or not 0 <= index < len(template):
            raise HTTPException(400, f"Invalid pin ID {pin_id!r}.")
        label, _, _, header = template[index]
        pin_metadata[pin_id] = {"pin_id": pin_id, "label": label, "header": header}
    try:
        return probe_jobs.start(
            payload.board,
            payload.pin_ids,
            manual_mode=payload.manual_mode,
            pin_metadata=pin_metadata,
        )
    except JobBusyError as exc:
        raise HTTPException(409, str(exc)) from exc
    except (PinCoordinateMapError, SerialConfigurationError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/jobs/{job_id}")
def probe_job_status(job_id: str, request: Request):
    require_local(request)
    job = probe_jobs.get(job_id)
    if job is None:
        raise HTTPException(404, "Probe job not found.")
    return job


@app.post("/api/jobs/{job_id}/ready")
def confirm_manual_probe_position(job_id: str, request: Request):
    require_local(request)
    try:
        return probe_jobs.confirm_manual_ready(job_id)
    except ManualJobStateError as exc:
        raise HTTPException(409, str(exc)) from exc


# --------------------------------------------------------------------------
# Pairing: network addresses, QR code, session
# --------------------------------------------------------------------------

IPHONE_HOTSPOT = ipaddress.ip_network("172.20.10.0/28")


def lan_addresses() -> list[str]:
    """IPv4 addresses a phone could reach, best first (iPhone hotspot, then private LANs)."""
    found = []
    for addrs in psutil.net_if_addrs().values():
        for a in addrs:
            if a.family.name != "AF_INET":
                continue
            ip = ipaddress.ip_address(a.address)
            if ip.is_loopback or ip.is_link_local:
                continue
            found.append(ip)

    def rank(ip):
        if ip in IPHONE_HOTSPOT:
            return 0
        return 1 if ip.is_private else 2

    return [str(ip) for ip in sorted(set(found), key=rank)]


def phone_url(ip: str) -> str:
    return f"http://{ip}:{PORT}/phone?s={SESSION_CODE}"


def require_local(request: Request):
    """Pairing details are only shown to the laptop itself, not to other devices on the network."""
    if request.client is None or not ipaddress.ip_address(request.client.host).is_loopback:
        raise HTTPException(403, "Open this page on the laptop running the server (http://localhost).")


@app.get("/api/session")
def session(request: Request):
    require_local(request)
    ips = lan_addresses()
    return {
        "code": SESSION_CODE,
        "urls": [{"ip": ip, "url": phone_url(ip), "hotspot": ipaddress.ip_address(ip) in IPHONE_HOTSPOT}
                 for ip in ips],
        "phone_connected": hub.phone_count() > 0,
    }


@app.get("/api/qr.svg")
def qr(request: Request, ip: str):
    require_local(request)
    if ip not in lan_addresses():
        raise HTTPException(404, "Unknown address")
    svg = io.BytesIO()
    segno.make(phone_url(ip), error="m").save(svg, kind="svg", scale=8, border=2,
                                              dark="#111111", light="#ffffff")
    return Response(svg.getvalue(), media_type="image/svg+xml")


# --------------------------------------------------------------------------
# Live hub: laptop pages and phone pages exchange events over WebSockets
# --------------------------------------------------------------------------

class Hub:
    def __init__(self):
        self.laptops: set[WebSocket] = set()
        self.phones: dict[WebSocket, str] = {}
        self.latest_jpg: bytes | None = None
        self.latest_id = 0

    def phone_count(self):
        return len(self.phones)

    async def send_all(self, sockets, msg: dict):
        for ws in list(sockets):
            try:
                await ws.send_json(msg)
            except Exception:
                pass

    async def phone_status(self):
        await self.send_all(self.laptops, {"type": "phone_status", "connected": self.phone_count() > 0,
                                           "devices": list(self.phones.values())})


hub = Hub()


def check_code(code: str | None):
    if not code or not secrets.compare_digest(code, SESSION_CODE):
        raise HTTPException(403, "Invalid or expired pairing code. Scan the QR code on the laptop again.")


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket, role: str, s: str | None = None):
    host = ws.client.host if ws.client else ""
    if role == "laptop":
        ok = ipaddress.ip_address(host).is_loopback
    elif role == "phone":
        ok = bool(s) and secrets.compare_digest(s, SESSION_CODE)
    else:
        ok = False
    if not ok:
        await ws.close(code=4403)
        return

    await ws.accept()
    if role == "laptop":
        hub.laptops.add(ws)
        await ws.send_json({"type": "phone_status", "connected": hub.phone_count() > 0,
                            "devices": list(hub.phones.values())})
    else:
        hub.phones[ws] = ws.headers.get("user-agent", "phone")
        await hub.phone_status()

    try:
        while True:
            msg = await ws.receive_json()
            if role == "laptop" and msg.get("type") == "capture_request":
                await hub.send_all(hub.phones, {"type": "capture_request"})
            elif msg.get("type") == "ping":
                await ws.send_json({"type": "pong"})
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        if role == "laptop":
            hub.laptops.discard(ws)
        else:
            hub.phones.pop(ws, None)
            await hub.phone_status()


@app.get("/api/phone/hello")
def phone_hello(s: str | None = None):
    check_code(s)
    return {"ok": True, "boards": list(pf.BOARDS)}


@app.post("/api/phone/upload")
async def phone_upload(s: str, image: UploadFile = File(...), board: str = "metro_grand_central"):
    check_code(s)
    data = await image.read()
    await hub.send_all(hub.laptops, {"type": "phone_photo_received", "bytes": len(data)})
    try:
        result, jpg = await run_in_threadpool(run_detection, data, board)
    except HTTPException as e:
        await hub.send_all(hub.laptops, {"type": "detection_failed", "detail": e.detail})
        raise

    hub.latest_id += 1
    hub.latest_jpg = jpg
    await hub.send_all(hub.laptops, {"type": "detection_result", "image_id": hub.latest_id, "result": result})
    return {"detected": result["detected"], "total": len(result["pins"]), "detect_ms": result["detect_ms"],
            "width": result["width"], "height": result["height"],
            "pins": [{"pin_id": p["pin_id"], "label": p["label"], "x": p["x"], "y": p["y"],
                      "detected": p["detected"]}
                     for p in result["pins"]]}


@app.get("/api/latest.jpg")
def latest_image(request: Request):
    require_local(request)
    if hub.latest_jpg is None:
        raise HTTPException(404, "No photo yet")
    return Response(hub.latest_jpg, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@app.get("/phone")
def phone_page():
    return FileResponse(ROOT / "static" / "phone.html", headers={"Cache-Control": "no-store"})


app.mount("/", StaticFiles(directory=ROOT / "static", html=True), name="static")


def main():
    global PORT
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=PORT)
    args = ap.parse_args()
    PORT = args.port

    print(f"\n  Pin Finder running.\n  Laptop:  http://localhost:{PORT}")
    ips = lan_addresses()
    if ips:
        print(f"  Phone:   scan the QR code on the laptop page ({phone_url(ips[0])})")
    else:
        print("  Phone:   no network found - turn on the phone's Personal Hotspot and connect the laptop to it")
    print()
    # 0.0.0.0 so the phone can reach the laptop over the hotspot/LAN.
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
