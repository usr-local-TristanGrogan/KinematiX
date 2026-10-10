# How it works

The goal: a robot arm with an oscilloscope probe on its tip moves to chosen pins
of a microcontroller and measures the signals on them. The board always sits in
the same place relative to the arm, so the camera isn't used to *locate* the
board. It's used so a person can **see every pin labelled and pick which ones
the arm should probe**.

```
 iPhone (browser page)              Laptop (Python server)                 Laptop (browser page)
 ┌──────────────────┐   photo    ┌──────────────────────────┐  WebSocket  ┌────────────────────┐
 │ TAKE PHOTO       │ ─────────> │ FastAPI                  │ ──────────> │ QR code, status    │
 │ (native camera)  │            │  └ pin_finder.find_pins  │             │ photo + pin boxes  │
 │ result: 92/92    │ <───────── │     (OpenCV)             │ <────────── │ pin table, select  │
 └──────────────────┘  summary   └──────────────────────────┘  "request   └────────────────────┘
        ▲                                                         photo"
        └──────────── Personal Hotspot over USB (or same Wi-Fi) ────────────
```

## 1. Starting it

```bash
python run.py          # Windows: py run.py
```

`run.py` works the same on macOS, Windows and Linux. It checks for Python 3.10+,
creates a `.venv`, installs the requirements the first time (and again only
when a requirements file changes), starts the server and opens
`http://localhost:8000` in the browser.

The server listens on every network interface (`0.0.0.0`, port 8000) so the
phone can reach it.

## 2. Connecting the phone

1. On the iPhone, turn on **Settings > Personal Hotspot** and plug it into the
   laptop with the USB cable. The laptop gets an address like `172.20.10.2` on
   that link. (Being on the same Wi-Fi also works.)
2. The laptop page shows a **QR code**. The server lists the laptop's network
   addresses (with `psutil`), puts the iPhone hotspot address (`172.20.10.x`)
   first, and encodes `http://<laptop-ip>:8000/phone?s=<pairing-code>` in the QR
   code (drawn with `segno`). If there are several networks you can choose one
   from the dropdown.
3. Scan it with the iPhone Camera app. The phone page opens and both screens
   show **CONNECTED**.

**Pairing code:** a random code is generated each time the server starts.
Without it the phone can't upload photos or connect. The QR code, pairing code
and photos are only served to the laptop itself (localhost), not to other
devices on the network. After a server restart the phone says "pairing
expired", and you scan the new QR code.

**Live link:** both pages keep a WebSocket open to the server (`/ws`). The
server tells the laptop when a phone connects or disconnects, when a photo
arrives, and when detection finishes. Both pages reconnect automatically after
the phone screen locks, the page reloads or the server restarts.

## 3. Taking a photo

The phone page is a single large **TAKE PHOTO** button. It's a file input with
`capture="environment"`, which opens the iPhone's own Camera app. That gives
full resolution, tap-to-focus and correct exposure, and it works over plain
HTTP. A live camera view inside the browser would need HTTPS certificates,
which is why the aiming happens in the Camera app.

After the shot:

1. The phone uploads the photo to `POST /api/phone/upload` (with a progress bar).
2. The server runs detection (below) in a worker thread.
3. The phone shows the result, such as **92/92 PINS**, with a preview dot on
   every pin, or a "not found" message with a tip.
4. The laptop receives the result over the WebSocket, loads the analysed image
   and draws all the boxes and labels.

**REQUEST PHOTO** on the laptop (or Space) sends a `capture_request` to the
phone, which shows a flashing "LAPTOP REQUESTED A PHOTO" banner. The phone still
has to tap the button, because browsers only open the camera after a tap.

**UPLOAD PHOTO** on the laptop runs detection on a saved file (JPEG, PNG or
HEIC), with no phone needed.

## 4. The laptop page

- The photo with a box and label on every pin, coloured by header (digital,
  analog, power, the double row, ICSP).
  - **Solid boxes / `OK`:** the pin was matched to a pad actually seen in the
    photo.
  - **Dashed boxes / `EST`:** the position was inferred from the board layout.
- Hover a box to see the pin name; click to select (Shift/Cmd/Ctrl-click for
  several). The selection is what the arm will probe.
- **FILTER** by pin name (`D13`, `A`, `GND`) or header (`POWER`, `ANALOG`).
- Scroll to zoom, drag to pan, `0` to reset the view, Esc to clear the selection.

Front end: plain HTML/CSS/JavaScript (`website/static/`), with no build step or
Node. Back end: FastAPI + uvicorn (`website/server.py`).

## 5. Pin detection (`computer-vision/pin_finder.py`)

The detector doesn't try to recognise pins one by one. It knows the exact
layout of the board, so it finds the board, lines up a **pin template** with
the gold pads it can see, and reads every pin position from the fitted
template. That's why it can still label pins hidden by glare or a shadow.

### The template

`metro_grand_central_template()` lists all 92 pins of the Adafruit Metro M4
Grand Central (headers D0–D53, A0–A15, power, ICSP) as coordinates in a flat
**reference frame** of 2000 × 1050 units. This is the board seen straight on,
with pin pitch ≈ 50 units (= 2.54 mm). Supporting another board means adding a
template and a board colour to `BOARDS`.

### Steps

1. **Load and orient.** JPEG, PNG or HEIC (via `pillow-heif`), rotated
   according to the phone's EXIF orientation.
2. **White balance.** The brightest, least-saturated pixels are assumed to be
   white and the colour channels are scaled to match. Phone cameras and
   webcams tint scenes differently, and this undoes most of it.
3. **Find board candidates.** Three independent methods, each giving the
   board's four corners:
   - **Fixed colour:** pixels in the board's purple HSV range.
   - **Learned colour:** find the largest cluster of gold, pad-sized blobs,
     sample the board colour between them (ignoring gold, white silkscreen and
     dark parts), and keep every pixel close to that colour. This adapts to
     odd lighting or other board colours.
   - **Pad cluster:** fit a rectangle around the gold pad cluster itself and
     extend it to the full board outline using the known layout. This works
     even when the board is too dim or washed out to segment by colour,
     because the headers run along the board edges.

   For the colour masks, the blob containing the most gold pads becomes the
   board; `minAreaRect` gives its corners.
4. **Straighten.** For each candidate, and both 180° orientations, a
   perspective warp maps the board into the 2000 × 1050 reference frame, so
   the photo angle no longer matters.
5. **Find pads.** In the warped image, gold pixels and bright glare spots are
   grouped into blobs; their centres are the candidate pad positions.
6. **Fit the template** in increasingly flexible steps, re-matching template
   pins to the nearest pad after each one:
   - **Similarity** (shift, rotate, scale)
   - **Affine** (plus shear/stretch)
   - **Homography** (full perspective, only once most pins have matched)

   Each step uses RANSAC, so wrong matches (a screw hole, a chip leg) are
   ignored.
7. **Pick the best.** The candidate and orientation whose fitted template
   lands on the most real pads wins.
8. **Map back.** Each pin's fitted position and a box of ±0.4 pitch around it
   are transformed back into the original photo's pixel coordinates.
9. **Sanity check.** If fewer than 30% of pins matched real pads, the server
   rejects the result ("too few pads found") rather than showing labels that
   might be wrong.

Typical runtime is 0.2–1 s for a 12 MP photo on a laptop.

### Taking a good photo

- Hold the phone about **20–30 cm** above the board; the main lens can't focus
  closer than that. Tap the board to focus.
- Get the **whole board** in frame, roughly from above.
- Even light helps; dim photos still work but match fewer pads directly.

## 6. API (for the arm and waveform code)

| Endpoint | Who | What |
| --- | --- | --- |
| `POST /api/detect` | anyone on the laptop | multipart `image` → pins JSON (`?return_image=1` adds the analysed image) |
| `POST /api/phone/upload?s=CODE` | paired phone | same detection; result is also pushed to the laptop page |
| `GET /api/session` | laptop only | pairing code + phone URLs |
| `GET /api/qr.svg?ip=…` | laptop only | QR code image |
| `GET /api/latest.jpg` | laptop only | last analysed phone photo |
| `WS /ws?role=laptop` / `?role=phone&s=CODE` | both pages | live events (`phone_status`, `detection_result`, `capture_request`, …) |

Each pin in the result looks like:

```json
{"label": "D13", "header": "digital_high", "x": 1171.8, "y": 2478.1,
 "detected": true, "box": [[x, y], [x, y], [x, y], [x, y]]}
```

`x`/`y` are photo pixels, used for display and selection. Because the board's
position relative to the arm is fixed, the arm should use the pin's position
**on the board** (millimetres from the template, see
`board_pose.template_mm`) plus the known board placement, not photo pixels.

## 7. Running on another laptop

What's needed:

- Python 3.10+ (python.org installer; on Windows tick "Add Python to PATH").
- Internet once, for the first `python run.py` to install packages.
- **Firewall:** the first time, macOS asks "allow incoming connections for
  Python?" and Windows Defender asks the same. Click **Allow**; on Windows
  tick **Private** and **Public**, because the iPhone hotspot is often
  classified as public.
- **Windows + iPhone hotspot over USB:** needs Apple's USB driver, which comes
  with the **Apple Devices** app (or iTunes) from the Microsoft Store. Otherwise
  use the hotspot over Wi-Fi, or put both on the same Wi-Fi.
- Port 8000 busy? Run `python run.py --port 8080`.

## Repo layout

```
run.py                      one-command launcher
computer-vision/
  pin_finder.py             detector (template, masks, fitting)
  board_pose.py             pin positions in mm; optional camera pose
  calibrate.py              optional camera calibration
  samples/                  test photos
website/
  server.py                 FastAPI server, pairing, WebSocket hub
  static/index.html, app.js laptop page
  static/phone.html, phone.js  phone page
```
