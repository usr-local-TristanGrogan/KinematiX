"""Local web UI for pin detection.

  python website/server.py          # then open http://localhost:8000
"""

from __future__ import annotations

import base64
import io
import sys
import time
from dataclasses import asdict
from pathlib import Path

import cv2
import uvicorn
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.staticfiles import StaticFiles

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent / "computer-vision"))
import pin_finder as pf  # noqa: E402

app = FastAPI(title="Pin Finder")

# Below this the layout fit can lock onto the wrong pads (seen with blurry frames).
MIN_DETECTED_FRACTION = 0.3


@app.get("/api/boards")
def boards():
    return {"boards": list(pf.BOARDS)}


@app.post("/api/detect")
async def detect(image: UploadFile = File(...), board: str = "metro_grand_central",
                 return_image: bool = False):
    if board not in pf.BOARDS:
        raise HTTPException(400, f"Unknown board '{board}'")
    data = await image.read()
    try:
        img = pf.load_image(io.BytesIO(data))
    except Exception:
        raise HTTPException(400, "Could not read image (JPEG, PNG or HEIC expected).")

    t0 = time.perf_counter()
    hint = ("Make sure the image is in focus (phone ~20-30 cm away; the main lens can't focus closer) "
            "and the whole board is visible.")
    try:
        pins, dbg = pf.find_pins(img, board)
    except RuntimeError as e:
        raise HTTPException(422, f"{e} {hint}")
    ms = (time.perf_counter() - t0) * 1000

    n_det = sum(p.detected for p in pins)
    if n_det < MIN_DETECTED_FRACTION * len(pins):
        raise HTTPException(422, f"Only {n_det}/{len(pins)} pads found, too few to trust the result. {hint}")

    result = {
        "board": board,
        "width": img.shape[1],
        "height": img.shape[0],
        "detect_ms": round(ms, 1),
        "board_corners": dbg["corners"].tolist(),
        "pins": [asdict(p) for p in pins],
    }
    if return_image:
        # Browsers can't display HEIC and may rotate differently, so send back exactly what was analysed.
        ok, jpg = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 90])
        result["image_jpeg"] = base64.b64encode(jpg.tobytes()).decode()
    return result


app.mount("/", StaticFiles(directory=ROOT / "static", html=True), name="static")


if __name__ == "__main__":
    print("Pin Finder running at http://localhost:8000")
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="warning")
