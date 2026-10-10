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
