# Website: Pin Finder UI

The laptop shows a QR code; the phone scans it, takes photos with its native
camera, and every pin of the microcontroller is labelled with a bounding box on
both screens. See [`../HOW_IT_WORKS.md`](../HOW_IT_WORKS.md) for the full description.

- **Backend:** FastAPI (Python). Calls `computer-vision/pin_finder.py` directly.
- **Frontend:** plain HTML/CSS/JS in `static/`, served by FastAPI. No build step.

## Run

From the repo root:

```bash
python run.py            # Windows: py run.py
```

This creates `.venv`, installs dependencies on first run, starts the server and
opens **http://localhost:8000**.

## Connect the phone

1. iPhone: **Settings > Personal Hotspot** on, plug it into the laptop by USB
   (or put phone and laptop on the same Wi-Fi).
2. Scan the QR code on the laptop page with the Camera app.
3. Tap **TAKE PHOTO**, hold 20–30 cm above the board, tap to focus, shoot.

First run: allow Python through the firewall when macOS / Windows asks
(on Windows tick both Private and Public). Windows needs the Apple Devices app
(or iTunes) for the iPhone USB hotspot.

## Using it

- **REQUEST PHOTO** (or Space): prompt the phone to take a photo.
- **UPLOAD PHOTO**: run detection on a saved photo (JPEG, PNG or HEIC).
- Hover a box to see the pin; click to select (Shift/Cmd/Ctrl-click for several).
- **FILTER**: type a pin name prefix (`D1`, `A`, `GND`) or a header name (`POWER`, `ANALOG`).
- Scroll to zoom, drag to pan, `0` to reset, Esc to clear the selection.

Solid boxes are pins matched to a detected pad (`OK`); dashed boxes are
positions inferred from the board layout (`EST`).

## API

`POST /api/detect` (multipart field `image`) returns:

```json
{
  "width": 3024, "height": 4032, "detect_ms": 56.5, "detected": 92,
  "board_corners": [[x, y], ...],
  "pins": [{"label": "SDA", "header": "digital_high", "x": 1171.8, "y": 2478.1,
            "detected": true, "box": [[x, y], [x, y], [x, y], [x, y]]}, ...]
}
```

Add `?return_image=1` to also get the analysed image back as base64 JPEG.
Phone pairing endpoints are listed in `HOW_IT_WORKS.md`.
