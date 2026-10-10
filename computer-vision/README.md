# Computer Vision: pin finder

Finds and labels every pin of a microcontroller board in a phone photo
(currently: Adafruit Metro M4 Grand Central / Arduino Mega layout).

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r computer-vision/requirements.txt
```

## Usage

```bash
python computer-vision/pin_finder.py computer-vision/samples/IMG_4899.HEIC \
    --out annotated.jpg --json pins.json --warp-out rectified.jpg

# label only specific pins
python computer-vision/pin_finder.py photo.jpg --highlight SDA SCL D13
```

Outputs:
- `annotated.jpg`: the photo with each pin labelled
- `pins.json`: `label`, `header`, `x`, `y` (photo pixels), `detected` per pin
- `rectified.jpg`: top-down view of the board with pins marked

## How it works

1. Find the board by its solder-mask colour and warp it to a fixed-size top-down view.
2. Overlay the board's known pin layout, trying both 180° orientations and keeping the one that lands on more detected pads.
3. Fit a homography (RANSAC) from the layout to the detected gold pads so every pin lands on the real geometry.
4. Map pin positions back to the original photo.

To support another board, add a template function and an entry in `BOARDS` in `pin_finder.py`.

## Board pose (distance and orientation from the camera)

```bash
cd computer-vision
python board_pose.py samples/IMG_4899.HEIC --out pose.jpg --json pose.json
```

Uses the detected pins plus the board's known physical layout to solve for the
board's 3D position (`cv2.solvePnP`). Output is in the camera frame (X right,
Y down, Z out of the lens, mm), with the origin at the centre of the pin layout.

Camera intrinsics come from the photo's EXIF focal length by default. For
accurate distances, calibrate the camera with a checkerboard and pass `--calib`.
The iPhone switches lenses (e.g. ultra-wide macro below ~15 cm), which changes
the intrinsics, so keep the camera distance and lens fixed in the robot setup.

`--pin D13 SDA` prints each pin's 3D position in the camera frame; `--json`
includes all pins under `pins_3d`.

## Camera calibration

```bash
python calibrate.py make-board --out checkerboard.png   # print at 100%, measure a square
python calibrate.py live --camera 0 --square-mm 20      # SPACE to capture 15-25 views, Q to finish
python calibrate.py photos calib/*.HEIC --square-mm 20  # or from phone photos
```

Calibrate with the same camera, lens and resolution used at runtime. Writes
`camera_calib.npz`.

## Live view

```bash
python live.py --camera 0 --calib camera_calib.npz --pin D13 SDA
```

Shows detected pins, board axes, and the 3D position of the requested pins.
The iPhone appears as a camera via Continuity Camera; the terminal (or Cursor)
needs camera permission in System Settings > Privacy & Security > Camera.
