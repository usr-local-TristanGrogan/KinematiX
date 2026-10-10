"""Locate and label the pins of a microcontroller board in a phone photo.

Pipeline:
  1. Load the photo (HEIC/JPEG/PNG), honouring EXIF rotation.
  2. Find the board by its solder-mask colour and rectify it to a top-down view.
  3. Overlay a pin template for the board (known header layout), trying both
     180-degree orientations and keeping the one that best matches real pads.
  4. Snap each template pin to the nearest detected pad, then fit a homography
     from template -> detected pads, so every pin (detected or not) lands on
     the board's actual geometry.
  5. Map everything back to the original photo coordinates.

Usage:
  python pin_finder.py IMG_4899.HEIC --out annotated.jpg --json pins.json
  python pin_finder.py IMG_4899.HEIC --highlight SDA SCL D13
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, asdict

import cv2
import numpy as np
from PIL import Image, ImageOps

try:
    import pillow_heif

    pillow_heif.register_heif_opener()
except ImportError:
    pass


# --------------------------------------------------------------------------
# Board profiles
# --------------------------------------------------------------------------
# Template coordinates are in a reference frame where the rectified board is
# REF_W x REF_H pixels, laid out in the standard Arduino orientation:
# USB on the left, digital headers along the top, power/analog along the bottom.

REF_W, REF_H = 2000, 1050


def _row(labels, x0, y0, dx, dy, header):
    return [(lab, x0 + i * dx, y0 + i * dy, header) for i, lab in enumerate(labels)]


def metro_grand_central_template():
    p = 50.2  # 0.1" pitch in reference pixels
    pins = []
    pins += _row(["SCL", "SDA", "AREF", "GND"] + [f"D{n}" for n in range(13, 7, -1)],
                 368, 75, p, 0, "digital_high")
    pins += _row([f"D{n}" for n in range(7, -1, -1)], 901, 75, p, 0, "digital_low")
    pins += _row([f"D{n}" for n in range(14, 22)], 1355, 75, p, 0, "comms")

    vp = 48.9
    pins += _row(["5V"] + [f"D{n}" for n in range(22, 53, 2)] + ["GND"],
                 1855, 77, 0, vp, "double_row_even")
    pins += _row(["5V"] + [f"D{n}" for n in range(23, 54, 2)] + ["GND"],
                 1906, 77, 0, vp, "double_row_odd")

    bp = 49.4
    pins += _row(["NC", "IOREF", "RESET", "3V3", "5V", "GND", "GND", "VIN"],
                 555, 1000, bp, 0, "power")
    pins += _row([f"A{n}" for n in range(0, 8)], 1000, 1000, bp, 0, "analog_low")
    pins += _row([f"A{n}" for n in range(8, 16)], 1445, 1000, bp, 0, "analog_high")

    pins += [("MISO", 1250, 469, "icsp"), ("5V", 1303, 469, "icsp"),
             ("SCK", 1250, 518, "icsp"), ("MOSI", 1303, 518, "icsp"),
             ("RESET", 1250, 566, "icsp"), ("GND", 1303, 566, "icsp")]
    return pins


BOARDS = {
    "metro_grand_central": {
        "template": metro_grand_central_template,
        # HSV range (OpenCV: H 0-180) of the solder mask
        "mask_hsv": ((115, 60, 40), (165, 255, 255)),
        "pitch": 50.0,
        # mm per reference pixel: x from the 0.1" header pitch, y from the
        # 1.9" spacing between the top and bottom header rows
        "mm_per_ref": (2.54 / 50.2, 48.26 / 925),
    },
}


# --------------------------------------------------------------------------
# Image helpers
# --------------------------------------------------------------------------

def load_image(path) -> np.ndarray:
    """Load from a path or file-like object (JPEG/PNG/HEIC), applying EXIF rotation."""
    im = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
    return cv2.cvtColor(np.array(im), cv2.COLOR_RGB2BGR)


def find_board(img: np.ndarray, mask_hsv) -> np.ndarray:
    """Return the board's 4 corners (float32, TL/TR/BR/BL with long side horizontal)."""
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, *mask_hsv)
    k = max(5, int(min(img.shape[:2]) * 0.01) | 1)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (k, k)))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (k // 2 | 1,) * 2))
    cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not cnts:
        raise RuntimeError("No board found - check lighting or the board's mask_hsv range.")
    c = max(cnts, key=cv2.contourArea)
    if cv2.contourArea(c) < 0.02 * mask.size:
        raise RuntimeError("Board region too small - move the phone closer.")

    box = cv2.boxPoints(cv2.minAreaRect(c))
    s, d = box.sum(1), np.diff(box, axis=1).ravel()
    tl, br, tr, bl = box[np.argmin(s)], box[np.argmax(s)], box[np.argmin(d)], box[np.argmax(d)]
    if np.linalg.norm(tr - tl) < np.linalg.norm(bl - tl):
        return np.float32([bl, tl, tr, br])
    return np.float32([tl, tr, br, bl])


def pad_mask(warp: np.ndarray) -> np.ndarray:
    """Binary mask of likely pads: gold/copper, or bright unsaturated (glare on tin)."""
    hsv = cv2.cvtColor(warp, cv2.COLOR_BGR2HSV)
    gold = cv2.inRange(hsv, (10, 40, 110), (40, 255, 255))
    shiny = cv2.inRange(hsv, (0, 0, 200), (180, 70, 255))
    return cv2.bitwise_or(gold, shiny)


def pad_centroids(mask: np.ndarray, pitch: float) -> np.ndarray:
    n, _, stats, cent = cv2.connectedComponentsWithStats(mask)
    lo, hi = (0.06 * pitch) ** 2, (1.2 * pitch) ** 2
    keep = [i for i in range(1, n) if lo < stats[i, cv2.CC_STAT_AREA] < hi]
    return cent[keep].astype(np.float32) if keep else np.zeros((0, 2), np.float32)


def snap(template_xy: np.ndarray, pads: np.ndarray, radius: float):
    """For each template point, index of nearest pad within radius (or -1)."""
    if len(pads) == 0:
        return np.full(len(template_xy), -1), np.full(len(template_xy), np.inf)
    d = np.linalg.norm(template_xy[:, None, :] - pads[None, :, :], axis=2)
    idx, dist = d.argmin(1), d.min(1)
    idx[dist > radius] = -1
    return idx, dist


# --------------------------------------------------------------------------
# Main detection
# --------------------------------------------------------------------------

@dataclass
class Pin:
    label: str
    header: str
    x: float          # pixel coords in the original photo
    y: float
    detected: bool    # True if a real pad was found here (vs. inferred from layout)
    box: list         # 4 [x, y] corners of the pin's bounding box in the photo


def find_pins(img: np.ndarray, board: str = "metro_grand_central"):
    prof = BOARDS[board]
    template = prof["template"]()
    labels = [t[0] for t in template]
    headers = [t[3] for t in template]
    txy = np.float32([[t[1], t[2]] for t in template])
    pitch = prof["pitch"]

    corners = find_board(img, prof["mask_hsv"])
    dst = np.float32([[0, 0], [REF_W, 0], [REF_W, REF_H], [0, REF_H]])

    # Try both 180-degree orientations, keep whichever matches more pads.
    best = None
    for rot in (0, 2):
        src = np.roll(corners, -rot, axis=0)
        H = cv2.getPerspectiveTransform(src, dst)
        warp = cv2.warpPerspective(img, H, (REF_W, REF_H))
        pads = pad_centroids(pad_mask(warp), pitch)
        idx, _ = snap(txy, pads, 0.4 * pitch)
        score = int((idx >= 0).sum())
        if best is None or score > best[0]:
            best = (score, H, warp, pads, idx)
    score, H, warp, pads, idx = best

    # Refine: homography from template -> matched pads, then re-snap tighter.
    for radius in (0.4 * pitch, 0.3 * pitch):
        ok = idx >= 0
        if ok.sum() >= 8:
            R, _ = cv2.findHomography(txy[ok], pads[idx[ok]], cv2.RANSAC, 0.2 * pitch)
            if R is not None:
                txy = cv2.perspectiveTransform(txy[None], R)[0]
        idx, _ = snap(txy, pads, radius)

    # Report the fitted layout position: glare can split a pad into fragments,
    # so individual blob centroids are noisier than the global fit.
    ok = idx >= 0
    warp_xy = txy

    Hinv = np.linalg.inv(H)
    photo_xy = cv2.perspectiveTransform(warp_xy[None], Hinv)[0]
    half = 0.4 * pitch
    square = np.float32([[-half, -half], [half, -half], [half, half], [-half, half]])
    boxes = cv2.perspectiveTransform((warp_xy[:, None, :] + square[None]).reshape(1, -1, 2), Hinv)
    boxes = boxes.reshape(-1, 4, 2)
    pins = [Pin(l, h, float(x), float(y), bool(o), b.round(1).tolist())
            for l, h, (x, y), o, b in zip(labels, headers, photo_xy, ok, boxes)]
    return pins, {"warp": warp, "warp_xy": warp_xy, "corners": corners, "H": H}


# --------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------

HEADER_COLOURS = {
    "digital_high": (255, 200, 0), "digital_low": (255, 120, 0), "comms": (0, 200, 255),
    "double_row_even": (255, 0, 200), "double_row_odd": (200, 80, 255), "power": (0, 0, 255), "analog_low": (0, 255, 0),
    "analog_high": (0, 180, 90), "icsp": (255, 255, 255),
}


def annotate(img, pins, highlight=()):
    out = img.copy()
    r = max(4, int(min(img.shape[:2]) * 0.004))
    font = 0.0005 * min(img.shape[:2])
    hl = {h.upper() for h in highlight}
    for p in pins:
        c = HEADER_COLOURS.get(p.header, (255, 255, 255))
        pt = (int(p.x), int(p.y))
        thick = -1 if p.detected else max(2, r // 3)
        if hl and p.label.upper() not in hl:
            cv2.circle(out, pt, r // 2, (90, 90, 90), -1)
            continue
        cv2.circle(out, pt, r * (2 if hl else 1), c, thick)
        cv2.putText(out, p.label, (pt[0] + r + 2, pt[1] - r), cv2.FONT_HERSHEY_SIMPLEX,
                    font * (1.6 if hl else 1), (0, 0, 0), max(3, r // 2), cv2.LINE_AA)
        cv2.putText(out, p.label, (pt[0] + r + 2, pt[1] - r), cv2.FONT_HERSHEY_SIMPLEX,
                    font * (1.6 if hl else 1), c, max(1, r // 5), cv2.LINE_AA)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("image")
    ap.add_argument("--board", default="metro_grand_central", choices=list(BOARDS))
    ap.add_argument("--out", default="annotated.jpg")
    ap.add_argument("--json", help="write pin list as JSON")
    ap.add_argument("--warp-out", help="also save the rectified top-down board image")
    ap.add_argument("--highlight", nargs="*", default=[], help="only label these pins, e.g. SDA SCL D13")
    args = ap.parse_args()

    img = load_image(args.image)
    pins, dbg = find_pins(img, args.board)

    n_det = sum(p.detected for p in pins)
    print(f"Found board; {n_det}/{len(pins)} pins matched to real pads "
          f"({len(pins) - n_det} inferred from layout).")
    for h in args.highlight:
        for p in pins:
            if p.label.upper() == h.upper():
                print(f"  {p.label:6s} ({p.header}) at x={p.x:.0f}, y={p.y:.0f}")

    cv2.imwrite(args.out, annotate(img, pins, args.highlight))
    print(f"Annotated image -> {args.out}")
    if args.warp_out:
        w = dbg["warp"].copy()
        for p, (x, y) in zip(pins, dbg["warp_xy"]):
            cv2.circle(w, (int(x), int(y)), 10, HEADER_COLOURS[p.header], -1 if p.detected else 2)
        cv2.imwrite(args.warp_out, w)
    if args.json:
        with open(args.json, "w") as f:
            json.dump([asdict(p) for p in pins], f, indent=2)
        print(f"Pin list -> {args.json}")


if __name__ == "__main__":
    main()
