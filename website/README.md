# Website: Pin Finder UI

Local web app: live view of the phone camera, capture a snapshot, and see every
pin of the microcontroller labelled with a bounding box.

- **Backend:** FastAPI (Python). Calls `computer-vision/pin_finder.py` directly.
- **Frontend:** plain HTML/CSS/JS in `static/`, served by FastAPI. No build step.

## Run

From the repo root:

```bash
python3 -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r website/requirements.txt
python website/server.py
```

Open **http://localhost:8000** in Chrome, Edge or Safari. Allow camera access when asked.

## Connecting the phone

The site uses whatever webcams the computer exposes, so the phone just has to
appear as a webcam:

| Computer | iPhone | Android |
| --- | --- | --- |
| macOS | Continuity Camera (built in, macOS 13+; works over the USB cable) | Camo or DroidCam (USB) |
| Windows | Camo (free app on phone + PC, USB) | Phone Link "connected camera" (Windows 11) or Camo / DroidCam (USB) |

Then press **REFRESH** and pick the phone in the camera list (it's selected
automatically if its name contains iPhone / Camo / DroidCam etc.).

## Using it

- **CAPTURE** (or Space): freeze the current frame and run detection.
- **LIVE** (or L): back to the live view.
- **UPLOAD**: run detection on a saved photo (JPEG, PNG or HEIC).
- Hover a box to see the pin; click to select (Shift/Cmd/Ctrl-click for several).
- **FILTER**: type a pin name prefix (`D1`, `A`, `GND`) or a header name (`POWER`, `ANALOG`).
- Scroll to zoom, drag to pan, `0` to reset, Esc to clear the selection.

Solid boxes are pins matched to a detected pad (`OK`); dashed boxes are
positions inferred from the board layout (`EST`).

## API

`POST /api/detect` (multipart field `image`) returns:

```json
{
  "width": 3024, "height": 4032, "detect_ms": 56.5,
  "board_corners": [[x, y], ...],
  "pins": [{"label": "SDA", "header": "digital_high", "x": 1171.8, "y": 2478.1,
            "detected": true, "box": [[x, y], [x, y], [x, y], [x, y]]}, ...]
}
```

Add `?return_image=1` to also get the analysed image back as base64 JPEG.
